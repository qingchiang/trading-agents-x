"""Connection management through the shared configuration interface."""

from tradingagents.configuration.models import ConfigurationPatch
from tradingagents.configuration.settings import AppSettings
from tradingagents.domain.runs import AnalysisRequest
from tradingagents.persistence.configuration import ConfigurationStore
from tradingagents.persistence.migrations import upgrade_database


def test_independent_connections_bind_roles_without_exposing_credentials(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    saved = store.save(
        ConfigurationPatch(
            revision=0,
            connection_changes=[
                {
                    "action": "create",
                    "id": "quick-endpoint",
                    "name": "Quick endpoint",
                    "preset": "openai_compatible",
                    "credentials": {"api_key": "quick-private"},
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://quick.example/v1",
                    },
                },
                {
                    "action": "create",
                    "id": "deep-endpoint",
                    "name": "Deep endpoint",
                    "preset": "openai_compatible",
                    "credentials": {"api_key": "deep-private"},
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://deep.example/v1",
                    },
                },
            ],
            values={'models': {'quick': {'connection_id': "quick-endpoint"}, 'deep': {'connection_id': "deep-endpoint"}}},
        ),
        initialize=True,
    )
    assert "quick-private" not in saved.model_dump_json()
    assert "deep-private" not in saved.model_dump_json()
    _, run = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    assert run.quick_binding.connection.id == "quick-endpoint"
    assert run.deep_binding.connection.id == "deep-endpoint"
    assert store.reveal_connection("quick-endpoint", "api_key") == "quick-private"
    assert store.reveal_connection("deep-endpoint", "api_key") == "deep-private"


def test_key_rotation_is_scoped_and_endpoint_changes_block_execution(tmp_path):
    import pytest

    from tradingagents.configuration.errors import ProviderConfigurationChanged
    from tradingagents.credentials import credential, use_credentials
    from tradingagents.llm.models import credential_name

    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(
            revision=0,
            connection_changes=[
                {
                    "action": "create",
                    "id": "local",
                    "name": "Local",
                    "preset": "openai_compatible",
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://first.example/v1",
                    },
                    "credentials": {"api_key": "first-key"},
                }
            ],
            values={'models': {'quick': {'connection_id': "local"}, 'deep': {'connection_id': "local"}}},
        ),
        initialize=True,
    )
    _, run = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    with use_credentials(store.execution_credentials(run)):
        store.save(
            ConfigurationPatch(
                revision=1,
                connection_changes=[
                    {
                        "action": "update",
                        "id": "local",
                        "credentials": {"api_key": "second-key"},
                    }
                ],
            )
        )
        assert credential(credential_name("local", "api_key")) == "first-key"
    with use_credentials(store.execution_credentials(run)):
        assert credential(credential_name("local", "api_key")) == "second-key"
    store.save(
        ConfigurationPatch(
            revision=2,
            connection_changes=[
                {
                    "action": "update",
                    "id": "local",
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://second.example/v1",
                    },
                }
            ],
        )
    )
    with pytest.raises(ProviderConfigurationChanged):
        store.execution_credentials(run)


def configured_store(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(
            revision=0,
            connection_changes=[
                {
                    "action": "create",
                    "id": "primary",
                    "name": "Primary",
                    "preset": "openai_compatible",
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://primary.example/v1",
                    },
                    "credentials": {"api_key": "primary-secret"},
                },
                {
                    "action": "create",
                    "id": "secondary",
                    "name": "Secondary",
                    "preset": "openai_compatible",
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://secondary.example/v1",
                    },
                    "credentials": {"api_key": "secondary-secret"},
                },
            ],
            values={'models': {'quick': {'connection_id': "primary"}, 'deep': {'connection_id': "secondary"}}},
        ),
        initialize=True,
    )
    return settings, store


def test_disabled_connection_retains_attempts_but_is_not_admitted_for_new_runs(tmp_path):
    import pytest

    _, store = configured_store(tmp_path)
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    _, retained = store.resolve_request(request)
    store.save(
        ConfigurationPatch(
            revision=1,
            values={'models': {'quick': {'connection_id': "secondary"}}},
            connection_changes=[{"action": "update", "id": "primary", "enabled": False}],
        )
    )
    assert store.execution_credentials(retained)
    with pytest.raises(ValueError, match="disabled"):
        store.resolve_request(request.model_copy(update={'models': {'quick': {'connection_id': "primary"}}}))
    assert not store.read().connections["primary"].selectable


def test_delete_blocks_active_runs_and_clears_keys_after_terminal_history(tmp_path):
    import pytest

    from tradingagents.persistence.repository import RunRepository

    settings, store = configured_store(tmp_path)
    request, retained = store.resolve_request(
        AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    )
    repository = RunRepository(settings)
    run, _ = repository.create_run(request, retained.snapshot())
    store.save(ConfigurationPatch(revision=1, values={'models': {'quick': {'connection_id': "secondary"}}}))
    with pytest.raises(ValueError, match="unfinished Runs"):
        store.save(
            ConfigurationPatch(
                revision=2, connection_changes=[{"action": "delete", "id": "primary"}]
            )
        )
    assert store.reveal_connection("primary", "api_key") == "primary-secret"
    repository.request_cancel(run.id)
    store.save(
        ConfigurationPatch(revision=2, connection_changes=[{"action": "delete", "id": "primary"}])
    )
    assert "primary" not in store.read().connections
    assert "primary-secret" not in store.credentials().values()
    assert repository.get_run(run.id) is not None
    with pytest.raises(ValueError, match="deleted"):
        store.execution_credentials(retained)
    with pytest.raises(ValueError, match="already exists"):
        store.save(
            ConfigurationPatch(
                revision=3,
                connection_changes=[{"action": "create", "id": "primary", "name": "Primary"}],
            )
        )


def test_two_role_clients_receive_distinct_keys_and_native_interfaces(tmp_path, monkeypatch):
    from tradingagents.credentials import use_credentials
    from tradingagents.llm.runtime import create_run_llms

    _, store = configured_store(tmp_path)
    store.save(
        ConfigurationPatch(
            revision=1,
            connection_changes=[
                {
                    "action": "update",
                    "id": "secondary",
                    "compatibility": "anthropic",
                    "key_required": True,
                    "discovery": "anthropic",
                    "transport": {"kind": "anthropic", "base_url": "https://anthropic.example"},
                }
            ],
            values={'models': {'deep': {'model': "claude-sonnet-4-6", 'reasoning_effort': "high"}}},
        )
    )
    _, run = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    captured = []

    class FakeClient:
        def get_llm(self):
            return captured[-1]

    def factory(provider, model, base_url, **kwargs):
        captured.append({"provider": provider, "model": model, "url": base_url, **kwargs})
        return FakeClient()

    monkeypatch.setattr("tradingagents.llm.connections.create_llm_client", factory)
    with use_credentials(store.execution_credentials(run)):
        clients = create_run_llms(run)
    assert clients.quick["api_key"] == "primary-secret"
    assert clients.deep["api_key"] == "secondary-secret"
    assert clients.deep["provider"] == "anthropic"
    assert clients.deep["effort"] == "high"
    assert clients.quick["url"] == "https://primary.example/v1"


def test_incremental_does_not_require_unused_quick_credentials(tmp_path):
    _, store = configured_store(tmp_path)
    store.save(
        ConfigurationPatch(
            revision=1,
            connection_changes=[
                {
                    "action": "update",
                    "id": "primary",
                    "key_required": True,
                    "credentials": {"api_key": None},
                }
            ],
        )
    )
    _, run = store.resolve_request(
        AnalysisRequest(
            ticker="GOOG",
            analysis_date="2026-09-10",
            research_kind="incremental",
            full_baseline_run_id="baseline",
        )
    )
    assert store.execution_credentials(run)


def test_connection_model_discovery_is_isolated_and_only_relevant_changes_expire_cache(tmp_path):
    from tradingagents.llm.model_discovery import ModelDiscoveryService

    settings, store = configured_store(tmp_path)
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "test-model"}]}

    class HTTP:
        def get(self, url, **kwargs):
            calls.append((url, kwargs["headers"]))
            return Response()

    service = ModelDiscoveryService(settings, configuration=store, session=HTTP())
    assert service.discover_connection("primary").source == "live"
    assert service.discover_connection("secondary").source == "live"
    assert calls[0][1]["Authorization"] == "Bearer primary-secret"
    assert calls[1][1]["Authorization"] == "Bearer secondary-secret"
    store.save(ConfigurationPatch(revision=1, values={"output_language": "ja"}))
    assert service.discover_connection("primary").source == "cache"
    store.save(
        ConfigurationPatch(
            revision=2,
            connection_changes=[
                {"action": "update", "id": "primary", "credentials": {"api_key": "rotated"}}
            ],
        )
    )
    assert service.discover_connection("primary").source == "live"
    assert calls[-1][1]["Authorization"] == "Bearer rotated"
    assert service.discover_connection("secondary").source == "cache"




def test_deleted_connection_cannot_be_rebound_by_environment_import(tmp_path):
    from tradingagents.configuration.models import ImportRequest
    _, store = configured_store(tmp_path)
    store.save(ConfigurationPatch(revision=1, connection_changes=[{"action": "delete", "id": "default"}]))
    preview = store.preview_import(ImportRequest(primary="OPENAI_API_KEY=fake-import-key", revision=2))
    assert preview.issues
    assert "fake-import-key" not in preview.model_dump_json()


def test_connection_save_is_atomic_with_bad_fields_and_stale_revision(tmp_path):
    import pytest

    _, store = configured_store(tmp_path)
    with pytest.raises(ValueError):
        store.save(
            ConfigurationPatch(
                revision=1,
                connection_changes=[
                    {
                        "action": "update",
                        "id": "primary",
                        "credentials": {"api_key": "should-rollback"},
                    },
                    {"action": "update", "id": "secondary", "compatibility": "missing-policy"},
                ],
            )
        )
    assert store.reveal_connection("primary", "api_key") == "primary-secret"
    assert store.read().revision == 1
    with pytest.raises(ValueError, match="revision"):
        store.save(
            ConfigurationPatch(
                revision=0, connection_changes=[{"action": "delete", "id": "primary"}]
            )
        )


def test_role_connection_overrides_are_independent(tmp_path):

    _, store = configured_store(tmp_path)
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10", profile="standard", models={
        "quick": {"connection_id": "primary"}, "deep": {"connection_id": "secondary"},
    })
    _, resolved = store.resolve_request(request)
    assert resolved.quick_binding.connection.id == "primary"
    assert resolved.deep_binding.connection.id == "secondary"
    assert resolved.profile.value == "standard"


def test_sdk_receives_scoped_credentials_and_never_environment_fallback(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pytest

    from tradingagents.credentials import use_credentials
    from tradingagents.llm.runtime import create_run_llms

    _, store = configured_store(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-must-not-be-used")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "ambient-must-not-be-used")
    monkeypatch.setattr(
        "tradingagents.llm.openai_client.LocalCompatibleChatOpenAI",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    _, run = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    with use_credentials(store.execution_credentials(run)):
        clients = create_run_llms(run)
        assert clients.quick.api_key == "primary-secret"
        assert clients.deep.api_key == "secondary-secret"
    store.save(
        ConfigurationPatch(
            revision=1,
            connection_changes=[
                {"action": "update", "id": "primary", "credentials": {"api_key": None}}
            ],
        )
    )
    with use_credentials(store.execution_credentials(run)):
        assert create_run_llms(run).quick.api_key == "EMPTY"
    store.save(
        ConfigurationPatch(
            revision=2,
            connection_changes=[{"action": "update", "id": "primary", "key_required": True}],
        )
    )
    _, required = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    with pytest.raises(ValueError, match="api_key"):
        store.execution_credentials(required)


def test_concurrent_execution_contexts_do_not_share_keys(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    from tradingagents.credentials import use_credentials
    from tradingagents.llm.models import credential_name
    from tradingagents.llm.runtime import create_run_llms

    _, store = configured_store(tmp_path)
    _, run = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    monkeypatch.setattr(
        "tradingagents.llm.openai_client.LocalCompatibleChatOpenAI",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    barrier = Barrier(2)

    def execute(prefix):
        credentials = {
            credential_name("primary", "api_key"): prefix + "-quick",
            credential_name("secondary", "api_key"): prefix + "-deep",
        }
        with use_credentials(credentials):
            barrier.wait(timeout=5)
            clients = create_run_llms(run)
            return clients.quick.api_key, clients.deep.api_key

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, ("first", "second")))
    assert results == [("first-quick", "first-deep"), ("second-quick", "second-deep")]
