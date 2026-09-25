from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

from tradingagents.configuration.settings import AppSettings
from tradingagents.llm.model_discovery import ModelDiscoveryService
from tradingagents.llm.provider_registry import PROVIDER_REGISTRY


class FakeResponse:
    def __init__(self, payload: dict[str, Any], *, error: Exception | None = None):
        self.payload = payload
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(
        self,
        responses: list[FakeResponse] | Callable[..., FakeResponse],
    ):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if callable(self.responses):
            return self.responses(url, **kwargs)
        return self.responses.pop(0)


def _settings(tmp_path: Path, **env: str) -> AppSettings:
    return AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_DATABASE_PATH": str(tmp_path / "models.db"),
            **env,
        },
        load_env_files=False,
    )


def _service(
    *,
    provider="openai",
    api_key=None,
    quick="gpt-5.4-mini",
    deep="gpt-5.5",
    base_url=None,
    region=None,
    **kwargs,
):
    from tradingagents.llm.models import DiscoverySnapshot, preset_connection

    connection = preset_connection(provider, identity=provider)
    transport = connection.transport.model_dump()
    if base_url:
        transport["base_url"] = base_url
    if region:
        transport["region"] = region
    connection = type(connection).model_validate(
        {**connection.model_dump(), "transport": transport}
    )
    snapshot = DiscoverySnapshot(
        connection, {"api_key": api_key} if api_key else {}, {"quick": quick, "deep": deep}
    )
    return ModelDiscoveryService(lambda identity: snapshot, **kwargs)


def test_provider_registry_covers_every_runtime_provider() -> None:
    assert {
        "openai",
        "anthropic",
        "google",
        "azure",
        "bedrock",
        "ollama",
        "openai_compatible",
        "openrouter",
    } <= set(PROVIDER_REGISTRY)


def test_openai_compatible_discovery_paginates_filters_and_caches(
    tmp_path: Path,
) -> None:
    now = [0.0]
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [
                        {"id": "gpt-5.4-mini"},
                        {
                            "id": "audio-only",
                            "output_modalities": ["audio"],
                        },
                    ],
                    "has_more": True,
                }
            ),
            FakeResponse(
                {
                    "data": [
                        {
                            "id": "text-model",
                            "output_modalities": ["text"],
                        }
                    ],
                    "has_more": False,
                }
            ),
            FakeResponse({"data": [{"id": "refreshed-model"}]}),
        ]
    )
    service = _service(
        provider="openai",
        api_key="private-key",
        quick="gpt-5.4-mini",
        deep="gpt-5.5",
        session=session,
        clock=lambda: now[0],
    )

    live = service.discover_connection("openai")
    cached = service.discover_connection("openai")
    now[0] = 301.0
    refreshed = service.discover_connection("openai")

    assert live.source == "live"
    assert cached.source == "cache"
    assert refreshed.source == "live"
    assert [model.id for model in live.models] == [
        "gpt-5.4-mini",
        "gpt-5.5",
        "text-model",
    ]
    assert "audio-only" not in {model.id for model in live.models}
    assert next(model for model in live.models if model.id == "gpt-5.4-mini").reasoning_efforts == (
        "provider_default",
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    assert next(model for model in live.models if model.id == "text-model").reasoning_efforts == (
        "provider_default",
    )
    assert "refreshed-model" in {model.id for model in refreshed.models}
    assert len(session.calls) == 3
    assert session.calls[0]["timeout"] == 5.0
    assert session.calls[0]["headers"]["Authorization"] == "Bearer private-key"
    assert session.calls[1]["params"] == {"after": "audio-only"}


def test_deepseek_catalog_exposes_only_effective_reasoning_levels(
    tmp_path: Path,
) -> None:
    service = _service(
        provider="deepseek",
        api_key="private-key",
        quick="deepseek-v4-flash",
        deep="deepseek-v4-flash",
        session=FakeSession([]),
    )

    models = service._normalize_models(
        "deepseek",
        [
            ("deepseek-v4-flash", "supported"),
            ("deepseek-v4-pro", "supported"),
        ],
    )

    assert {model.id: model.reasoning_efforts for model in models} == {
        "deepseek-v4-flash": (
            "provider_default",
            "low",
            "high",
            "max",
        ),
        "deepseek-v4-pro": ("provider_default", "high", "max"),
    }


def test_refresh_bypasses_five_minute_cache(tmp_path: Path) -> None:
    session = FakeSession(
        [
            FakeResponse({"data": [{"id": "first"}]}),
            FakeResponse({"data": [{"id": "second"}]}),
        ]
    )
    service = _service(provider="xai", api_key="key", quick="first", deep="first", session=session)

    assert "first" in {model.id for model in service.discover_connection("xai").models}
    assert "second" in {
        model.id for model in service.discover_connection("xai", refresh=True).models
    }
    assert len(session.calls) == 2


def test_failure_is_sanitized_and_keeps_configured_defaults(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_value = "private-key-in-provider-error"
    session = FakeSession(
        [
            FakeResponse(
                {},
                error=requests.RequestException(private_value),
            )
        ]
    )
    service = _service(
        provider="openai", api_key="key", quick="gpt-5.4-mini", deep="gpt-5.5", session=session
    )

    catalog = service.discover_connection("openai")

    assert catalog.source == "fallback"
    assert catalog.stale is True
    assert catalog.warning is not None
    assert catalog.warning.code == "model_discovery_unavailable"
    assert {model.id for model in catalog.models} == {
        "gpt-5.4-mini",
        "gpt-5.5",
    }
    assert private_value not in catalog.warning.message
    assert private_value not in caplog.text


def test_google_discovery_uses_generation_capability_and_pagination(
    tmp_path: Path,
) -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "models": [
                        {
                            "name": "models/gemini-text",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/embedding-only",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ],
                    "nextPageToken": "page-2",
                }
            ),
            FakeResponse({"models": [{"name": "models/gemini-next"}]}),
        ]
    )
    service = _service(
        provider="google",
        api_key="google-secret",
        quick="gemini-text",
        deep="gemini-next",
        session=session,
    )

    catalog = service.discover_connection("google")

    assert {model.id for model in catalog.models} == {
        "gemini-text",
        "gemini-next",
    }
    assert session.calls[0]["params"] == {"key": "google-secret"}
    assert session.calls[1]["params"] == {
        "key": "google-secret",
        "pageToken": "page-2",
    }


def test_anthropic_discovery_uses_last_id_pagination(tmp_path: Path) -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [{"id": "claude-sonnet-5"}],
                    "has_more": True,
                    "last_id": "claude-sonnet-5",
                }
            ),
            FakeResponse(
                {
                    "data": [{"id": "claude-opus-4-8"}],
                    "has_more": False,
                }
            ),
        ]
    )
    service = _service(
        provider="anthropic",
        api_key="anthropic-secret",
        quick="claude-sonnet-5",
        deep="claude-opus-4-8",
        session=session,
    )

    catalog = service.discover_connection("anthropic")

    assert {model.id for model in catalog.models} == {
        "claude-sonnet-5",
        "claude-opus-4-8",
    }
    assert session.calls[1]["params"] == {"after_id": "claude-sonnet-5"}
    assert session.calls[0]["headers"] == {
        "x-api-key": "anthropic-secret",
        "anthropic-version": "2023-06-01",
    }


def test_ollama_uses_native_tags_endpoint(tmp_path: Path) -> None:
    session = FakeSession([FakeResponse({"models": [{"name": "qwen3:latest"}]})])
    service = _service(
        provider="ollama",
        api_key=None,
        quick="qwen3:latest",
        deep="qwen3:latest",
        base_url="http://ollama.internal:11434/v1",
        session=session,
    )

    catalog = service.discover_connection("ollama")

    assert [model.id for model in catalog.models] == ["qwen3:latest"]
    assert session.calls[0]["url"] == "http://ollama.internal:11434/api/tags"


def test_bedrock_adapter_filters_non_text_models(tmp_path: Path) -> None:
    class Bedrock:
        def list_foundation_models(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "modelSummaries": [
                    {"modelId": "text-model", "outputModalities": ["TEXT"]},
                    {"modelId": "image-model", "outputModalities": ["IMAGE"]},
                ]
            }

    service = _service(
        provider="bedrock",
        api_key=None,
        quick="text-model",
        deep="text-model",
        region="ap-northeast-1",
        bedrock_client_factory=lambda region: (
            Bedrock() if region == "ap-northeast-1" else pytest.fail("unexpected region")
        ),
    )
    # The optional package may not be installed in core-only test environments;
    # this unit test exercises the adapter directly through its injected seam.
    from tradingagents.llm.model_discovery import _DiscoveryClient

    snapshot = service.snapshot("bedrock")
    models = _DiscoveryClient(
        snapshot.connection,
        snapshot.credentials,
        service.session,
        5,
        service.bedrock_client_factory,
    ).fetch()

    assert models == [("text-model", "supported")]


def test_database_discovery_uses_latest_connection_and_credentials_without_ambient_fallback(
    tmp_path, monkeypatch
):
    from tradingagents.configuration.models import ConfigurationPatch
    from tradingagents.persistence.configuration import ConfigurationStore
    from tradingagents.persistence.migrations import upgrade_database

    settings = _settings(tmp_path)
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(
            revision=0,
            connection_changes=[
                {
                    "action": "create",
                    "id": "default",
                    "preset": "openai",
                    "credentials": {"api_key": "first"},
                }
            ],
        ),
        initialize=True,
    )
    session = FakeSession(lambda *args, **kwargs: FakeResponse({"data": [{"id": "model"}]}))
    service = ModelDiscoveryService(store.connection_discovery_snapshot, session=session)
    assert service.discover_connection("default").source == "live"
    assert service.discover_connection("default").source == "cache"
    assert len(session.calls) == 1
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer first"
    store.save(
        ConfigurationPatch(
            revision=1,
            connection_changes=[
                {
                    "action": "update",
                    "id": "default",
                    "credentials": {"api_key": "second"},
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://relay.example/v1",
                    },
                }
            ],
        )
    )
    assert service.discover_connection("default").source == "live"
    assert session.calls[-1]["url"].startswith("https://relay.example/v1")
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer second"
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-not-allowed")
    store.save(
        ConfigurationPatch(
            revision=2,
            connection_changes=[
                {"action": "update", "id": "default", "credentials": {"api_key": None}}
            ],
        )
    )
    assert service.discover_connection("default").source == "fallback"
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "provider,root,path",
    [
        ("anthropic", "https://api.anthropic.com", "/v1/models"),
        ("google", "https://generativelanguage.googleapis.com", "/v1beta/models"),
    ],
)
def test_execution_and_discovery_use_one_api_version_segment(tmp_path, provider, root, path):
    from tradingagents.configuration.models import ConfigurationPatch
    from tradingagents.domain.runs import AnalysisRequest
    from tradingagents.persistence.configuration import ConfigurationStore
    from tradingagents.persistence.migrations import upgrade_database

    settings = _settings(tmp_path)
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(
            revision=0,
            values={"models": {role: {"connection_id": "selected"} for role in ("quick", "deep")}},
            connection_changes=[
                {
                    "action": "create",
                    "id": "selected",
                    "preset": provider,
                    "credentials": {"api_key": "offline-key"},
                }
            ],
        ),
        initialize=True,
    )
    _, run = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    assert run.deep_binding.connection.transport.base_url == root
    session = FakeSession([FakeResponse({"data": [], "models": []})])
    ModelDiscoveryService(store.connection_discovery_snapshot, session=session).discover_connection(
        "selected"
    )
    assert session.calls[0]["url"] == root + path
