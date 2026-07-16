"""Default pytest runs cannot inherit dotenv or developer API credentials."""

import os

_KEYS_DURING_COLLECTION = {
    env_var: os.environ.get(env_var)
    for env_var in (
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
    )
}


def test_dotenv_loading_is_disabled_before_application_imports():
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"


def test_default_tests_receive_placeholder_llm_keys():
    for env_var in (
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
    ):
        assert os.environ[env_var] == "placeholder"


def test_credentials_are_scrubbed_during_test_collection():
    assert set(_KEYS_DURING_COLLECTION.values()) == {"placeholder"}
