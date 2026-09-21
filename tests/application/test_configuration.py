"""Configuration behavior through the shared application interface."""

import pytest

from tradingagents.configuration.models import ConfigurationPatch, ImportRequest
from tradingagents.configuration.settings import AppSettings
from tradingagents.domain.runs import AnalysisRequest
from tradingagents.persistence.configuration import ConfigurationStore
from tradingagents.persistence.migrations import upgrade_database


def test_configuration_save_is_atomic_and_credentials_are_only_explicitly_revealed(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    initial = store.read()
    assert not initial.initialized
    saved = store.save(
        ConfigurationPatch(
            revision=initial.revision,
            values={"output_language": "ja"},
            credentials={"DEEPSEEK_API_KEY": "test-private-value"},
        )
    )
    assert saved.values.output_language == "ja"
    assert saved.credentials["DEEPSEEK_API_KEY"]
    assert "test-private-value" not in saved.model_dump_json()
    assert store.reveal("DEEPSEEK_API_KEY") == "test-private-value"
    with pytest.raises(ValueError, match="revision"):
        store.save(ConfigurationPatch(revision=initial.revision, values={"output_language": "en"}))
    assert store.read().values.output_language == "ja"


def test_import_preview_is_redacted_and_imported_values_do_not_follow_environment(tmp_path):
    settings = AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_LLM_PROVIDER": "deepseek",
            "TRADINGAGENTS_OUTPUT_LANGUAGE": "ja",
            "DEEPSEEK_API_KEY": "import-private-key",
            "TRADINGAGENTS_LLM_BACKEND_URL": "https://relay.example/v1",
        }
    )
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    assert store.read().values.llm_provider == "openai"
    preview = store.preview_import(ImportRequest())
    assert "import-private-key" not in preview.model_dump_json()
    store.apply_import(ImportRequest(fingerprint=preview.fingerprint))
    assert store.read().initialized
    request, resolved = store.resolve_request(
        AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    )
    assert resolved.llm_provider == "deepseek"
    assert resolved.backend_url == "https://relay.example/v1"
    assert request.output_language == "ja"
    store.save(ConfigurationPatch(revision=1, credentials={"DEEPSEEK_API_KEY": None}))
    assert store.reveal("DEEPSEEK_API_KEY") is None


def test_explicit_profile_overrides_database_default_and_bad_patch_is_atomic(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(revision=0, values={"profile": "deep", "analysts": ["news"]}),
        initialize=True,
    )
    implicit, _ = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    explicit, _ = store.resolve_request(
        AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10", profile="standard")
    )
    assert implicit.profile.value == "deep"
    assert implicit.analysts == ("news",)
    assert explicit.profile.value == "standard"
    with pytest.raises(ValueError):
        store.save(
            ConfigurationPatch(
                revision=1,
                values={"data_vendors": {"news_data": "unknown"}},
                credentials={"DEEPSEEK_API_KEY": "bad-patch-key"},
            )
        )
    assert store.read().revision == 1
    assert store.reveal("DEEPSEEK_API_KEY") is None


def test_execution_credentials_rotate_without_rewriting_run_parameters(tmp_path):
    from tradingagents.configuration.errors import ProviderConfigurationChanged
    from tradingagents.credentials import credential, use_credentials

    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(revision=0, credentials={"OPENAI_API_KEY": "first"}), initialize=True
    )
    _, run = store.resolve_request(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    previous = credential("OPENAI_API_KEY")
    with use_credentials(store.execution_credentials(run)):
        store.save(ConfigurationPatch(revision=1, credentials={"OPENAI_API_KEY": "second"}))
        assert credential("OPENAI_API_KEY") == "first"
    assert store.execution_credentials(run)["OPENAI_API_KEY"] == "second"
    assert credential("OPENAI_API_KEY") == previous
    store.save(
        ConfigurationPatch(
            revision=2, values={"providers": {"openai": {"base_url": "https://new.example/v1"}}}
        )
    )
    with pytest.raises(ProviderConfigurationChanged):
        store.execution_credentials(run)


def test_worker_does_not_claim_tasks_before_configuration_is_initialized(tmp_path):
    from tradingagents.application.worker import AnalysisWorker

    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    worker = AnalysisWorker(settings)
    worker.repository.create_run(
        AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"),
        settings.default_run_settings.snapshot(),
    )
    assert worker.run_once() is False
    assert worker.repository.list_runs().items[0].status.value == "queued"


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "azure"])
def test_clients_never_fall_back_to_environment_credentials(monkeypatch, provider):
    from tradingagents.credentials import use_credentials
    from tradingagents.llm import create_llm_client
    from tradingagents.llm.api_key_env import get_api_key_env

    monkeypatch.setenv(get_api_key_env(provider), "ambient-key-must-not-be-used")
    with use_credentials({}), pytest.raises(ValueError, match="Settings"):
        create_llm_client(provider, "custom-model").get_llm()


def test_configuration_catalog_classifies_every_default_and_translates_fields():
    from tradingagents.configuration.catalog import configuration_schema
    from tradingagents.configuration.defaults import DEFAULT_CONFIG

    schema = configuration_schema()
    fields = {field.key: field for field in schema.fields}
    assert set(DEFAULT_CONFIG) - set(fields) == {
        "project_dir",
        "data_cache_dir",
        "backend_url",
        "news_selection_version",
    }
    for field in fields.values():
        assert set(field.label) == set(field.description) == {"en", "zh-CN", "ja"}
    assert fields["yahoo_news_candidate_limit"].maximum == 200
    assert fields["cn_news_candidate_limit"].maximum == 100
    assert fields["global_news_query_limit"].maximum == 5


def test_import_precedence_invalid_fields_and_stale_source(tmp_path):
    from pydantic import SecretStr

    from tradingagents.configuration.errors import ConfigurationConflict

    settings = AppSettings.from_env(
        environ={"TRADINGAGENTS_HOME": str(tmp_path), "OPENAI_API_KEY": "process-value"}
    )
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    source = ImportRequest(
        primary=SecretStr("OPENAI_API_KEY=primary-value\nTRADINGAGENTS_OUTPUT_LANGUAGE=ja"),
        enterprise=SecretStr("OPENAI_API_KEY=enterprise-value\nTRADINGAGENTS_OUTPUT_LANGUAGE=en"),
    )
    preview = store.preview_import(source)
    assert preview.values["output_language"] == "ja"
    changed = source.model_copy(
        update={"primary": SecretStr("OPENAI_API_KEY=changed"), "fingerprint": preview.fingerprint}
    )
    with pytest.raises(ConfigurationConflict):
        store.apply_import(changed)
    store.apply_import(source.model_copy(update={"fingerprint": preview.fingerprint}))
    assert store.reveal("OPENAI_API_KEY") == "process-value"
    invalid = ImportRequest(
        primary=SecretStr(
            "OLLAMA_BASE_URL=not-a-url\nTRADINGAGENTS_LLM_PROVIDER=unknown\nTRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS=-1"
        )
    )
    assert {issue.name for issue in store.preview_import(invalid).issues} == {
        "OLLAMA_BASE_URL",
        "TRADINGAGENTS_LLM_PROVIDER",
        "TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS",
    }


def test_cli_import_preview_does_not_create_database(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    import tradingagents.cli.main as cli

    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path / "new-home")})
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    result = CliRunner().invoke(cli.app, ["config", "import-env"])
    assert result.exit_code == 0
    assert not settings.database_path.exists()


def test_provider_patch_preserves_omitted_fields_and_reset_preserves_credentials(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(
            revision=0,
            values={
                "providers": {
                    "azure": {
                        "base_url": "https://azure.example",
                        "deployment": "research",
                        "api_version": "2024-10-21",
                    }
                }
            },
            credentials={"AZURE_OPENAI_API_KEY": "retained"},
        ),
        initialize=True,
    )
    saved = store.save(
        ConfigurationPatch(revision=1, values={"providers": {"azure": {"deployment": "new"}}})
    )
    assert saved.values.providers["azure"].base_url == "https://azure.example"
    assert saved.values.providers["azure"].api_version == "2024-10-21"
    assert saved.values.providers["azure"].deployment == "new"
    reset = store.save(ConfigurationPatch(revision=2, reset_fields=["providers"]))
    assert reset.values.providers == {}
    assert store.reveal("AZURE_OPENAI_API_KEY") == "retained"


def test_checkpoint_and_failure_records_redact_execution_credentials(tmp_path):
    from langgraph.graph import END, START, StateGraph

    from tradingagents.credentials import use_credentials
    from tradingagents.persistence._repository_common import _sanitize_text
    from tradingagents.persistence.checkpoints import CredentialSafeSqliteSaver

    secret = "fake-credential-without-a-secret-prefix"
    with use_credentials({"OPENAI_API_KEY": secret}):
        assert secret not in _sanitize_text(f"Request rejected for {secret}")
        with CredentialSafeSqliteSaver.from_conn_string(str(tmp_path / "checkpoint.db")) as saver:
            builder = StateGraph(dict)

            def fail(_state):
                raise ValueError(f"Request rejected for {secret}")

            builder.add_node("provider", fail)
            builder.add_edge(START, "provider")
            builder.add_edge("provider", END)
            graph = builder.compile(checkpointer=saver)
            with pytest.raises(ValueError):
                graph.invoke({"input": "public"}, {"configurable": {"thread_id": "run"}})
            writes = saver.conn.execute("SELECT value FROM writes").fetchall()
            assert writes
            assert all(secret.encode() not in row[0] for row in writes)
