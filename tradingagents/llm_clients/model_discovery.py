"""Lazy, cached, and sanitized provider model discovery."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import requests

from tradingagents.application.settings import AppSettings

from .provider_registry import (
    PROVIDER_REGISTRY,
    ProviderAvailability,
    ProviderDefinition,
    get_provider_definition,
    provider_availability,
    resolve_provider_base_url,
)
from .reasoning_effort import known_model_effort_levels

logger = logging.getLogger(__name__)

ModelCompatibility = Literal["supported", "unknown"]
CatalogSource = Literal["live", "cache", "fallback"]


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    label: str
    compatibility: ModelCompatibility
    reasoning_efforts: tuple[str, ...]
    default_roles: tuple[Literal["quick", "deep"], ...] = ()


@dataclass(frozen=True)
class DiscoveryWarning:
    code: str
    message: str


@dataclass(frozen=True)
class ModelCatalog:
    provider: str
    models: tuple[DiscoveredModel, ...]
    source: CatalogSource
    fetched_at: datetime
    stale: bool = False
    warning: DiscoveryWarning | None = None


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    catalog: ModelCatalog


class UnknownProviderError(ValueError):
    """Raised when a model catalog is requested for an unsupported provider."""


class ModelDiscoveryService:
    """Discover provider models on demand with a five-minute memory cache."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        environ: Mapping[str, str] | None = None,
        session: requests.Session | None = None,
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        bedrock_client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.environ = environ
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.clock = clock
        self.now = now or (lambda: datetime.now(UTC))
        self.bedrock_client_factory = bedrock_client_factory
        self._cache: dict[tuple[str, str | None], _CacheEntry] = {}
        self._lock = Lock()

    def providers(self) -> dict[str, tuple[ProviderDefinition, ProviderAvailability]]:
        """Return every provider for Settings, including unavailable entries."""
        return {
            name: (
                definition,
                provider_availability(
                    definition,
                    self.settings,
                    self.environ,
                ),
            )
            for name, definition in PROVIDER_REGISTRY.items()
        }

    def discover(self, provider: str, *, refresh: bool = False) -> ModelCatalog:
        """Return a live, cached, or configured-default model catalog."""
        definition = get_provider_definition(provider)
        if definition is None:
            raise UnknownProviderError(provider)
        availability = provider_availability(
            definition,
            self.settings,
            self.environ,
        )
        base_url = resolve_provider_base_url(
            definition,
            self.settings,
            self.environ,
        )
        cache_key = (definition.name, _safe_endpoint_identity(base_url))
        now_monotonic = self.clock()
        if not refresh:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached and cached.expires_at > now_monotonic:
                return replace(cached.catalog, source="cache")

        if not availability.selectable:
            catalog = self._fallback_catalog(
                definition,
                warning=DiscoveryWarning(
                    code="provider_not_configured",
                    message=(
                        f"{definition.label} is not configured; "
                        "only configured defaults and a custom model ID are available."
                    ),
                ),
            )
        else:
            try:
                raw_models = self._fetch(definition, base_url)
                models = self._normalize_models(definition.name, raw_models)
                models = self._merge_configured_defaults(definition.name, models)
                catalog = ModelCatalog(
                    provider=definition.name,
                    models=models,
                    source="live",
                    fetched_at=self.now(),
                )
            except Exception as exc:
                logger.warning(
                    "Model discovery failed for provider %s (%s)",
                    definition.name,
                    type(exc).__name__,
                )
                catalog = self._fallback_catalog(
                    definition,
                    warning=DiscoveryWarning(
                        code="model_discovery_unavailable",
                        message=(
                            f"Could not refresh {definition.label} models. "
                            "Configured defaults and a custom model ID remain available."
                        ),
                    ),
                )

        with self._lock:
            self._cache[cache_key] = _CacheEntry(
                expires_at=now_monotonic + self.cache_ttl_seconds,
                catalog=catalog,
            )
        return catalog

    def _fetch(
        self,
        definition: ProviderDefinition,
        base_url: str | None,
    ) -> list[tuple[str, ModelCompatibility]]:
        if definition.adapter == "openai_compatible":
            return self._discover_openai_compatible(definition, base_url)
        if definition.adapter == "anthropic":
            return self._discover_anthropic(definition, base_url)
        if definition.adapter == "google":
            return self._discover_google(definition, base_url)
        if definition.adapter == "ollama":
            return self._discover_ollama(base_url)
        if definition.adapter == "bedrock":
            return self._discover_bedrock()
        if definition.adapter == "custom":
            return []
        raise RuntimeError("Unsupported discovery adapter")

    def _discover_openai_compatible(
        self,
        definition: ProviderDefinition,
        base_url: str | None,
    ) -> list[tuple[str, ModelCompatibility]]:
        if not base_url:
            raise RuntimeError("Provider endpoint is not configured")
        headers: dict[str, str] = {}
        api_key = self._env_value(definition.api_key_env)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        endpoint = f"{base_url.rstrip('/')}/models"
        models: list[tuple[str, ModelCompatibility]] = []
        after: str | None = None
        for _page in range(20):
            params = {"after": after} if after else None
            payload = self._get_json(endpoint, headers=headers, params=params)
            entries = payload.get("data", [])
            if not isinstance(entries, list):
                raise ValueError("Invalid model list response")
            for item in entries:
                parsed = _parse_model_item(item)
                if parsed is not None:
                    models.append(parsed)
            if not payload.get("has_more") or not entries:
                break
            last = entries[-1]
            after = str(last.get("id", "")).strip() if isinstance(last, dict) else ""
            if not after:
                break
        return models

    def _discover_anthropic(
        self,
        definition: ProviderDefinition,
        base_url: str | None,
    ) -> list[tuple[str, ModelCompatibility]]:
        if not base_url:
            raise RuntimeError("Provider endpoint is not configured")
        headers = {
            "x-api-key": self._env_value(definition.api_key_env) or "",
            "anthropic-version": "2023-06-01",
        }
        endpoint = f"{base_url.rstrip('/')}/models"
        models: list[tuple[str, ModelCompatibility]] = []
        after: str | None = None
        for _page in range(20):
            params = {"after_id": after} if after else None
            payload = self._get_json(endpoint, headers=headers, params=params)
            entries = payload.get("data", [])
            if not isinstance(entries, list):
                raise ValueError("Invalid model list response")
            for item in entries:
                parsed = _parse_model_item(item)
                if parsed is not None:
                    models.append(parsed)
            if not payload.get("has_more"):
                break
            after = str(payload.get("last_id", "")).strip()
            if not after:
                break
        return models

    def _discover_google(
        self,
        definition: ProviderDefinition,
        base_url: str | None,
    ) -> list[tuple[str, ModelCompatibility]]:
        if not base_url:
            raise RuntimeError("Provider endpoint is not configured")
        endpoint = f"{base_url.rstrip('/')}/models"
        models: list[tuple[str, ModelCompatibility]] = []
        page_token: str | None = None
        for _page in range(20):
            params = {"key": self._env_value(definition.api_key_env) or ""}
            if page_token:
                params["pageToken"] = page_token
            payload = self._get_json(endpoint, params=params)
            entries = payload.get("models", [])
            if not isinstance(entries, list):
                raise ValueError("Invalid model list response")
            for item in entries:
                if not isinstance(item, dict):
                    continue
                methods = item.get("supportedGenerationMethods")
                if isinstance(methods, list) and "generateContent" not in methods:
                    continue
                model_id = str(item.get("name", "")).removeprefix("models/").strip()
                if model_id:
                    models.append(
                        (
                            model_id,
                            "supported" if isinstance(methods, list) else "unknown",
                        )
                    )
            page_token = str(payload.get("nextPageToken", "")).strip()
            if not page_token:
                break
        return models

    def _discover_ollama(
        self,
        base_url: str | None,
    ) -> list[tuple[str, ModelCompatibility]]:
        if not base_url:
            raise RuntimeError("Provider endpoint is not configured")
        parsed = urlsplit(base_url)
        path = parsed.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[:-3]
        root = urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")
        payload = self._get_json(f"{root}/api/tags")
        entries = payload.get("models", [])
        if not isinstance(entries, list):
            raise ValueError("Invalid model list response")
        models = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("name") or item.get("model") or "").strip()
            if model_id:
                models.append((model_id, "unknown"))
        return models

    def _discover_bedrock(self) -> list[tuple[str, ModelCompatibility]]:
        region = (
            self._env_value("AWS_REGION")
            or self._env_value("AWS_DEFAULT_REGION")
            or "us-west-2"
        )
        if self.bedrock_client_factory is not None:
            client = self.bedrock_client_factory(region)
        else:
            import boto3

            client = boto3.client("bedrock", region_name=region)
        models: list[tuple[str, ModelCompatibility]] = []
        token: str | None = None
        for _page in range(20):
            kwargs = {"nextToken": token} if token else {}
            payload = client.list_foundation_models(**kwargs)
            entries = payload.get("modelSummaries", [])
            if not isinstance(entries, list):
                raise ValueError("Invalid model list response")
            for item in entries:
                if not isinstance(item, dict):
                    continue
                modalities = item.get("outputModalities")
                if isinstance(modalities, list) and "TEXT" not in modalities:
                    continue
                model_id = str(item.get("modelId", "")).strip()
                if model_id:
                    models.append(
                        (
                            model_id,
                            "supported"
                            if isinstance(modalities, list)
                            else "unknown",
                        )
                    )
            token = str(payload.get("nextToken", "")).strip()
            if not token:
                break
        return models

    def _get_json(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self.session.get(
            endpoint,
            headers=dict(headers or {}),
            params=dict(params or {}),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid model list response")
        return payload

    def _normalize_models(
        self,
        provider: str,
        raw_models: list[tuple[str, ModelCompatibility]],
    ) -> tuple[DiscoveredModel, ...]:
        unique: dict[str, ModelCompatibility] = {}
        for model_id, compatibility in raw_models:
            normalized = model_id.strip()
            if not normalized:
                continue
            existing = unique.get(normalized)
            if existing != "supported":
                unique[normalized] = compatibility
        return tuple(
            DiscoveredModel(
                id=model_id,
                label=model_id,
                compatibility=compatibility,
                reasoning_efforts=(
                    "provider_default",
                    *known_model_effort_levels(provider, model_id),
                ),
            )
            for model_id, compatibility in sorted(
                unique.items(),
                key=lambda item: item[0].casefold(),
            )
        )

    def _merge_configured_defaults(
        self,
        provider: str,
        models: tuple[DiscoveredModel, ...],
    ) -> tuple[DiscoveredModel, ...]:
        roles = self._default_roles(provider)
        by_id = {model.id: model for model in models}
        for model_id, default_roles in roles.items():
            if model_id in by_id:
                by_id[model_id] = replace(
                    by_id[model_id],
                    default_roles=default_roles,
                )
            else:
                by_id[model_id] = DiscoveredModel(
                    id=model_id,
                    label=model_id,
                    compatibility="unknown",
                    reasoning_efforts=(
                        "provider_default",
                        *known_model_effort_levels(provider, model_id),
                    ),
                    default_roles=default_roles,
                )
        return tuple(
            sorted(
                by_id.values(),
                key=lambda model: (
                    not bool(model.default_roles),
                    model.id.casefold(),
                ),
            )
        )

    def _fallback_catalog(
        self,
        definition: ProviderDefinition,
        *,
        warning: DiscoveryWarning,
    ) -> ModelCatalog:
        return ModelCatalog(
            provider=definition.name,
            models=self._merge_configured_defaults(definition.name, ()),
            source="fallback",
            fetched_at=self.now(),
            stale=True,
            warning=warning,
        )

    def _default_roles(
        self,
        provider: str,
    ) -> dict[str, tuple[Literal["quick", "deep"], ...]]:
        defaults = self.settings.default_run_settings
        if provider != defaults.llm_provider:
            return {}
        roles: dict[str, list[Literal["quick", "deep"]]] = {}
        roles.setdefault(defaults.quick_model, []).append("quick")
        roles.setdefault(defaults.deep_model, []).append("deep")
        return {model: tuple(values) for model, values in roles.items()}

    def _env_value(self, name: str | None) -> str | None:
        if name is None:
            return None
        if self.environ is not None:
            return self.environ.get(name)
        return os_environ_get(name)


def os_environ_get(name: str) -> str | None:
    """Small seam kept out of serialized settings and easy to isolate in tests."""
    import os

    return os.environ.get(name)


def _safe_endpoint_identity(base_url: str | None) -> str | None:
    """Cache by scheme/host/path while dropping query strings and credentials."""
    if not base_url:
        return None
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


def _parse_model_item(
    item: Any,
) -> tuple[str, ModelCompatibility] | None:
    if not isinstance(item, dict):
        return None
    model_id = str(item.get("id", "")).strip()
    if not model_id:
        return None

    methods = item.get("supported_generation_methods")
    if isinstance(methods, list):
        text_methods = {"generateContent", "chat.completions", "responses"}
        if not text_methods.intersection(str(method) for method in methods):
            return None
        return model_id, "supported"

    modalities = item.get("output_modalities")
    architecture = item.get("architecture")
    if not isinstance(modalities, list) and isinstance(architecture, dict):
        modalities = architecture.get("output_modalities")
    if isinstance(modalities, list):
        normalized = {str(modality).lower() for modality in modalities}
        if not normalized.intersection({"text", "string"}):
            return None
        return model_id, "supported"

    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("text_generation") is False:
        return None
    if isinstance(capabilities, dict) and capabilities.get("text_generation") is True:
        return model_id, "supported"
    return model_id, "unknown"
