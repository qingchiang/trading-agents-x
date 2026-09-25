"""Legacy environment names are accepted only by explicit configuration import."""

import pytest
from pydantic import SecretStr

from tradingagents.configuration.models import ImportRequest
from tradingagents.configuration.settings import AppSettings
from tradingagents.persistence.configuration import ConfigurationStore


def preview(tmp_path, **environment):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)}, load_env_files=False)
    request = ImportRequest(primary=SecretStr("\n".join(f"{name}={value}" for name, value in environment.items())))
    return ConfigurationStore(settings).preview_import(request)


def test_import_converts_roles_and_scalar_types(tmp_path):
    result = preview(tmp_path, TRADINGAGENTS_LLM_PROVIDER="google",
        TRADINGAGENTS_QUICK_THINK_LLM="gemini-3-flash-preview",
        TRADINGAGENTS_DEEP_THINK_LLM="gemini-3-pro-preview",
        TRADINGAGENTS_DEEP_REASONING_EFFORT="provider_default",
        TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS="14",
        TRADINGAGENTS_SOCIAL_LOOKBACK_DAYS="7")
    assert not result.issues
    assert result.values["models"]["deep"]["model"] == "gemini-3-pro-preview"
    assert result.values["models"]["deep"]["reasoning_effort"] == "provider_default"
    assert result.values["models"]["quick"]["model"] == "gemini-3-flash-preview"
    assert result.values["ticker_news_lookback_days"] == 14
    assert result.values["social_lookback_days"] == 7


def test_empty_import_values_do_not_overwrite_defaults(tmp_path):
    result = preview(tmp_path, TRADINGAGENTS_LLM_PROVIDER="", TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS="")
    assert not result.issues
    assert "models" not in result.values
    assert "ticker_news_lookback_days" not in result.values


@pytest.mark.parametrize("name,value", [
    ("TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS", "not-a-number"),
    ("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "3"),
    ("TRADINGAGENTS_CHECKPOINT_ENABLED", "true"),
    ("TRADINGAGENTS_NONEXISTENT_KEY", "oops"),
])
def test_invalid_or_retired_import_fields_are_reported(tmp_path, name, value):
    assert [issue.name for issue in preview(tmp_path, **{name: value}).issues] == [name]
