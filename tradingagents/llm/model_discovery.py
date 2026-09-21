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

from tradingagents.llm.models import DiscoverySnapshot, ModelConnection, connection_view
from tradingagents.llm.reasoning_effort import known_model_effort_levels

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
    connection_id: str
    models: tuple[DiscoveredModel, ...]
    source: CatalogSource
    fetched_at: datetime
    stale: bool = False
    warning: DiscoveryWarning | None = None


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    catalog: ModelCatalog


class ModelDiscoveryService:
    """Discover provider models on demand with a five-minute memory cache."""

    def __init__(
        self,
        snapshot: Callable[[str], DiscoverySnapshot],
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
        bedrock_client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.clock = clock
        self.now = now or (lambda: datetime.now(UTC))
        self.bedrock_client_factory = bedrock_client_factory
        self._cache: dict[tuple[str, int], _CacheEntry] = {}
        self._lock = Lock()

    def discover_connection(self, identity: str, *, refresh=False):
        snapshot = self.snapshot(identity)
        conn, auth, defaults = snapshot.connection, snapshot.credentials, snapshot.default_models
        key = (conn.id, conn.revision)
        with self._lock:
            for old in list(self._cache):
                if old[0] == conn.id and old != key:
                    del self._cache[old]
            cached = self._cache.get(key)
        if cached and not refresh and cached.expires_at > self.clock():
            return self._connection_defaults(
                replace(cached.catalog, source="cache"), conn, defaults
            )
        worker = _DiscoveryClient(
            conn, auth, self.session, self.timeout_seconds, self.bedrock_client_factory
        )
        warning = None
        models = ()
        if not connection_view(conn, auth).selectable or conn.discovery == "custom":
            warning = DiscoveryWarning(
                "provider_not_configured", "Complete this connection or enter a model ID manually"
            )
        else:
            try:
                models = self._normalize_models(conn.compatibility, worker.fetch())
            except Exception as exc:
                logger.warning("Connection model discovery failed (%s)", type(exc).__name__)
                warning = DiscoveryWarning(
                    "model_discovery_unavailable",
                    "Could not refresh models; enter a model ID manually",
                )
        catalog = ModelCatalog(
            identity, models, "fallback" if warning else "live", self.now(), bool(warning), warning
        )
        with self._lock:
            self._cache[key] = _CacheEntry(self.clock() + self.cache_ttl_seconds, catalog)
        return self._connection_defaults(catalog, conn, defaults)

    @staticmethod
    def _connection_defaults(catalog, conn, defaults):
        by_id = {model.id: model for model in catalog.models}
        for role in ("quick", "deep"):
            if role in defaults:
                model_id = defaults[role]
                existing = by_id.get(model_id)
                by_id[model_id] = DiscoveredModel(
                    model_id,
                    model_id,
                    existing.compatibility if existing else "unknown",
                    ("provider_default", *known_model_effort_levels(conn.compatibility, model_id)),
                    (*existing.default_roles, role) if existing else (role,),
                )
        return replace(
            catalog,
            models=tuple(
                sorted(
                    by_id.values(),
                    key=lambda model: (not bool(model.default_roles), model.id.casefold()),
                )
            ),
        )

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


class _DiscoveryClient:
    """Protocol requests using one immutable connection snapshot."""

    def __init__(
        self,
        connection: ModelConnection,
        credentials,
        session,
        timeout_seconds,
        bedrock_client_factory,
    ):
        self.connection = connection
        self.credentials = credentials
        self.session = session
        self.timeout_seconds = timeout_seconds
        self.bedrock_client_factory = bedrock_client_factory

    def fetch(self) -> list[tuple[str, ModelCompatibility]]:
        base_url = getattr(self.connection.transport, "base_url", None)
        if self.connection.discovery == "openai_compatible":
            return self._discover_openai_compatible(base_url)
        if self.connection.discovery == "anthropic":
            return self._discover_anthropic(base_url)
        if self.connection.discovery == "google":
            return self._discover_google(base_url)
        if self.connection.discovery == "ollama":
            return self._discover_ollama(base_url)
        if self.connection.discovery == "bedrock":
            return self._discover_bedrock()
        if self.connection.discovery == "custom":
            return []
        raise RuntimeError("Unsupported discovery adapter")

    def _discover_openai_compatible(
        self,
        base_url: str | None,
    ) -> list[tuple[str, ModelCompatibility]]:
        if not base_url:
            raise RuntimeError("Provider endpoint is not configured")
        headers: dict[str, str] = {}
        api_key = self.credentials.get("api_key")
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
        base_url: str | None,
    ) -> list[tuple[str, ModelCompatibility]]:
        if not base_url:
            raise RuntimeError("Provider endpoint is not configured")
        headers = {
            "x-api-key": self.credentials.get("api_key") or "",
            "anthropic-version": "2023-06-01",
        }
        root = base_url.rstrip("/")
        endpoint = f"{root}/models" if root.endswith("/v1") else f"{root}/v1/models"
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
        base_url: str | None,
    ) -> list[tuple[str, ModelCompatibility]]:
        if not base_url:
            raise RuntimeError("Provider endpoint is not configured")
        root = base_url.rstrip("/")
        endpoint = (
            f"{root}/models" if root.endswith(("/v1", "/v1beta")) else f"{root}/v1beta/models"
        )
        models: list[tuple[str, ModelCompatibility]] = []
        page_token: str | None = None
        for _page in range(20):
            params = {"key": self.credentials.get("api_key") or ""}
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
        from tradingagents.llm.connections import aws_client

        region = self.connection.transport.region
        client = (
            self.bedrock_client_factory(region)
            if self.bedrock_client_factory
            else aws_client(self.connection.transport.model_dump(), self.credentials, "bedrock")
        )
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
                            "supported" if isinstance(modalities, list) else "unknown",
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
