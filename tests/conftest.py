"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Set this before test modules import application code. python-dotenv honors the
# flag and therefore cannot pull private credentials from the project .env.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


def pytest_configure(config):
    for marker in ("unit", "integration", "live_llm", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_CREDENTIAL_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "MOONSHOT_API_KEY",
    "GROQ_API_KEY",
    "NVIDIA_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "JQUANTS_API_KEY",
    "EDINET_API_KEY",
    "FRED_API_KEY",
    "ESTAT_APP_ID",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)

# Capture only the credential needed by the currently supported live test.
# This happens after dotenv loading is disabled and before test modules are
# collected, so the value can only have come from the launching shell or its
# secret manager. All process-visible credentials are scrubbed immediately.
_EXPLICIT_LIVE_LLM_KEYS = {
    "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY"),
}
_EXPLICIT_INCREMENTAL_DATA_KEYS = {
    "JQUANTS_API_KEY": os.environ.get("JQUANTS_API_KEY"),
    "EDINET_API_KEY": os.environ.get("EDINET_API_KEY"),
}
for _env_var in _CREDENTIAL_ENV_VARS:
    os.environ[_env_var] = "placeholder"


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch, request):
    live_test = (
        request.node.get_closest_marker("live_llm") is not None
        and os.environ.get("RUN_LIVE_LLM_TESTS") == "1"
    )
    for env_var in _CREDENTIAL_ENV_VARS:
        # Reset credentials for every test in case another test mutated os.environ.
        monkeypatch.setenv(env_var, "placeholder")
    if live_test:
        for env_var, value in _EXPLICIT_LIVE_LLM_KEYS.items():
            if value and value != "placeholder":
                monkeypatch.setenv(env_var, value)
    incremental_live = (
        live_test
        and request.node.get_closest_marker("live_data") is not None
        and os.environ.get("RUN_LIVE_DATA_TESTS") == "1"
    )
    if incremental_live:
        for env_var, value in _EXPLICIT_INCREMENTAL_DATA_KEYS.items():
            if value and value != "placeholder":
                monkeypatch.setenv(env_var, value)


@pytest.fixture(autouse=True)
def _isolate_config():
    """Bind an isolated run-scoped dataflow config for every test."""
    import copy
    import tempfile

    import tradingagents.default_config as default_config
    from tradingagents.dataflows.config import bind_config, reset_config

    def _fresh(cache_dir):
        cfg = copy.deepcopy(default_config.DEFAULT_CONFIG)
        cfg["data_cache_dir"] = cache_dir
        return cfg

    with tempfile.TemporaryDirectory() as cache_dir:
        token = bind_config(_fresh(cache_dir), merge=False)
        try:
            yield
        finally:
            reset_config(token)


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client


@pytest.fixture
def app_settings(tmp_path: Path):
    from tradingagents.application.settings import AppSettings

    return AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_DATABASE_PATH": str(
                tmp_path / "tradingagents.db"
            ),
            "TRADINGAGENTS_CACHE_DIR": str(tmp_path / "cache"),
            "TRADINGAGENTS_RESEARCH_UPDATE_MODE": "shadow",
            "TRADINGAGENTS_EXPERIMENTAL_NMC_JP_WHITELIST": "6501.T",
        },
        load_env_files=False,
    )


@pytest.fixture
def repository(app_settings):
    from tradingagents.application.repository import RunRepository
    from tradingagents.persistence import upgrade_database

    upgrade_database(app_settings)
    return RunRepository(app_settings)
