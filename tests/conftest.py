"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os
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


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.

    Also point ``data_cache_dir`` at a throwaway dir so any on-disk cache a test
    exercises (the macro SeriesCache disk layer, the EDINET learned code map, …)
    never reads, writes, or deletes the user's real cache. Tests that need to
    inspect the cache still override it with their own tmp dir.
    """
    import copy
    import tempfile

    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    def _fresh():
        cfg = copy.deepcopy(default_config.DEFAULT_CONFIG)
        cfg["data_cache_dir"] = cache_dir
        return cfg

    with tempfile.TemporaryDirectory() as cache_dir:
        config_module._config = _fresh()
        yield
        config_module._config = _fresh()


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
