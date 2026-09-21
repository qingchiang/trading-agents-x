from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

from tradingagents.configuration.settings import AppSettings
from tradingagents.llm.model_discovery import ModelDiscoveryService
from tradingagents.llm.provider_registry import PROVIDER_REGISTRY, provider_availability


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


def test_provider_availability_requires_key_without_exposing_it(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    missing = provider_availability(
        PROVIDER_REGISTRY["openai"],
        settings,
        {},
    )
    configured = provider_availability(
        PROVIDER_REGISTRY["openai"],
        settings,
        {"OPENAI_API_KEY": "private-key"},
    )

    assert missing.configured is False
    assert missing.reason == "api_key_missing"
    assert configured.configured is True
    assert configured.api_key_configured is True
    assert "private-key" not in repr(configured)


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
    service = ModelDiscoveryService(
        _settings(tmp_path),
        environ={"OPENAI_API_KEY": "private-key"},
        session=session,
        clock=lambda: now[0],
    )

    live = service.discover("openai")
    cached = service.discover("openai")
    now[0] = 301.0
    refreshed = service.discover("openai")

    assert live.source == "live"
    assert cached.source == "cache"
    assert refreshed.source == "live"
    assert [model.id for model in live.models] == [
        "gpt-5.4-mini",
        "gpt-5.5",
        "text-model",
    ]
    assert "audio-only" not in {model.id for model in live.models}
    assert next(
        model for model in live.models if model.id == "gpt-5.4-mini"
    ).reasoning_efforts == (
        "provider_default",
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    assert next(
        model for model in live.models if model.id == "text-model"
    ).reasoning_efforts == ("provider_default",)
    assert "refreshed-model" in {model.id for model in refreshed.models}
    assert len(session.calls) == 3
    assert session.calls[0]["timeout"] == 5.0
    assert session.calls[0]["headers"]["Authorization"] == "Bearer private-key"
    assert session.calls[1]["params"] == {"after": "audio-only"}


def test_deepseek_catalog_exposes_only_effective_reasoning_levels(
    tmp_path: Path,
) -> None:
    service = ModelDiscoveryService(
        _settings(
            tmp_path,
            TRADINGAGENTS_LLM_PROVIDER="deepseek",
            TRADINGAGENTS_QUICK_THINK_LLM="deepseek-v4-flash",
            TRADINGAGENTS_DEEP_THINK_LLM="deepseek-v4-flash",
        ),
        environ={"DEEPSEEK_API_KEY": "private-key"},
        session=FakeSession([]),
    )

    models = service._normalize_models(
        "deepseek",
        [
            ("deepseek-v4-flash", "supported"),
            ("deepseek-v4-pro", "supported"),
        ],
    )

    assert {
        model.id: model.reasoning_efforts for model in models
    } == {
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
    service = ModelDiscoveryService(
        _settings(
            tmp_path,
            TRADINGAGENTS_LLM_PROVIDER="xai",
            TRADINGAGENTS_QUICK_THINK_LLM="first",
            TRADINGAGENTS_DEEP_THINK_LLM="first",
        ),
        environ={"XAI_API_KEY": "key"},
        session=session,
    )

    assert "first" in {model.id for model in service.discover("xai").models}
    assert "second" in {
        model.id for model in service.discover("xai", refresh=True).models
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
    service = ModelDiscoveryService(
        _settings(tmp_path),
        environ={"OPENAI_API_KEY": "key"},
        session=session,
    )

    catalog = service.discover("openai")

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
    service = ModelDiscoveryService(
        _settings(
            tmp_path,
            TRADINGAGENTS_LLM_PROVIDER="google",
            TRADINGAGENTS_QUICK_THINK_LLM="gemini-text",
            TRADINGAGENTS_DEEP_THINK_LLM="gemini-next",
        ),
        environ={"GOOGLE_API_KEY": "google-secret"},
        session=session,
    )

    catalog = service.discover("google")

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
    service = ModelDiscoveryService(
        _settings(
            tmp_path,
            TRADINGAGENTS_LLM_PROVIDER="anthropic",
            TRADINGAGENTS_QUICK_THINK_LLM="claude-sonnet-5",
            TRADINGAGENTS_DEEP_THINK_LLM="claude-opus-4-8",
        ),
        environ={"ANTHROPIC_API_KEY": "anthropic-secret"},
        session=session,
    )

    catalog = service.discover("anthropic")

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
    session = FakeSession(
        [FakeResponse({"models": [{"name": "qwen3:latest"}]})]
    )
    service = ModelDiscoveryService(
        _settings(
            tmp_path,
            TRADINGAGENTS_LLM_PROVIDER="ollama",
            TRADINGAGENTS_QUICK_THINK_LLM="qwen3:latest",
            TRADINGAGENTS_DEEP_THINK_LLM="qwen3:latest",
        ),
        environ={"OLLAMA_BASE_URL": "http://ollama.internal:11434/v1"},
        session=session,
    )

    catalog = service.discover("ollama")

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

    service = ModelDiscoveryService(
        _settings(
            tmp_path,
            TRADINGAGENTS_LLM_PROVIDER="bedrock",
            TRADINGAGENTS_QUICK_THINK_LLM="text-model",
            TRADINGAGENTS_DEEP_THINK_LLM="text-model",
        ),
        environ={
            "AWS_PROFILE": "research",
            "AWS_DEFAULT_REGION": "ap-northeast-1",
        },
        bedrock_client_factory=lambda region: (
            Bedrock()
            if region == "ap-northeast-1"
            else pytest.fail("unexpected region")
        ),
    )
    # The optional package may not be installed in core-only test environments;
    # this unit test exercises the adapter directly through its injected seam.
    models = service._discover_bedrock()

    assert models == [("text-model", "supported")]


def test_database_discovery_uses_latest_connection_and_credentials_without_ambient_fallback(tmp_path, monkeypatch):
    from tradingagents.configuration.models import ConfigurationPatch
    from tradingagents.persistence.configuration import ConfigurationStore
    from tradingagents.persistence.migrations import upgrade_database

    settings = _settings(tmp_path)
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(ConfigurationPatch(revision=0, credentials={"OPENAI_API_KEY": "first"}), initialize=True)
    session = FakeSession(lambda *args, **kwargs: FakeResponse({"data": [{"id": "model"}]}))
    service = ModelDiscoveryService(settings, configuration=store, session=session)
    assert service.discover("openai").source == "live"
    assert service.discover("openai").source == "cache"
    assert len(session.calls) == 1
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer first"
    store.save(ConfigurationPatch(revision=1, credentials={"OPENAI_API_KEY": "second"}, values={"providers": {"openai": {"base_url": "https://relay.example/v1"}}}))
    assert service.discover("openai").source == "live"
    assert session.calls[-1]["url"].startswith("https://relay.example/v1")
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer second"
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-not-allowed")
    store.save(ConfigurationPatch(revision=2, credentials={"OPENAI_API_KEY": None}))
    assert service.discover("openai").source == "fallback"
    assert len(session.calls) == 2


@pytest.mark.parametrize("provider,root,path", [
    ("anthropic", "https://api.anthropic.com", "/v1/models"),
    ("google", "https://generativelanguage.googleapis.com", "/v1beta/models"),
])
def test_execution_and_discovery_use_one_api_version_segment(tmp_path, provider, root, path):
    from tradingagents.configuration.models import ConfigurationPatch
    from tradingagents.domain.runs import AnalysisRequest
    from tradingagents.persistence.configuration import ConfigurationStore
    from tradingagents.persistence.migrations import upgrade_database

    settings = _settings(tmp_path)
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(ConfigurationPatch(revision=0, values={"llm_provider": provider}, credentials={PROVIDER_REGISTRY[provider].api_key_env: "offline-key"}), initialize=True)
    _, run = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    assert run.backend_url == root
    session = FakeSession([FakeResponse({"data": [], "models": []})])
    ModelDiscoveryService(settings, configuration=store, session=session).discover(provider)
    assert session.calls[0]["url"] == root + path
