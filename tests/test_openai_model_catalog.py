from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.model_catalog import get_model_options


def test_gpt_56_models_lead_openai_cli_lists():
    quick = [model for _, model in get_model_options("openai", "quick")]
    deep = [model for _, model in get_model_options("openai", "deep")]
    assert quick[:2] == ["gpt-5.6-luna", "gpt-5.6-terra"]
    assert deep[:2] == ["gpt-5.6-sol", "gpt-5.6-terra"]
    assert "gpt-5.4-mini" in quick
    assert "gpt-5.5-pro" in deep


def test_default_models_remain_unchanged():
    assert DEFAULT_CONFIG["quick_think_llm"] == "gpt-5.4-mini"
    assert DEFAULT_CONFIG["deep_think_llm"] == "gpt-5.5"
