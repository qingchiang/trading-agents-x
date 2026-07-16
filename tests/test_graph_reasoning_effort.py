"""Graph creates independent provider-native kwargs for quick and deep roles."""

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.reasoning_effort import RESOLVED_MARKER


def _graph(
    provider, quick_model, deep_model, quick_effort="low", deep_effort="high"
):
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {
        "llm_provider": provider,
        "quick_think_llm": quick_model,
        "deep_think_llm": deep_model,
        "quick_reasoning_effort": quick_effort,
        "deep_reasoning_effort": deep_effort,
        "openai_reasoning_effort": None,
        "google_thinking_level": None,
        "anthropic_effort": None,
        "temperature": 0.2,
        "llm_max_retries": 4,
    }
    return graph


@pytest.mark.parametrize(
    "provider,quick_model,deep_model,native",
    [
        ("openai", "gpt-5.6-luna", "gpt-5.6-sol", "reasoning_effort"),
        (
            "openai_compatible",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "reasoning_effort",
        ),
        ("azure", "gpt-5.6-luna", "gpt-5.6-sol", "reasoning_effort"),
        ("google", "gemini-3.5-flash", "gemini-3.1-pro-preview", "thinking_level"),
        ("anthropic", "claude-sonnet-5", "claude-fable-5", "effort"),
    ],
)
def test_graph_builds_independent_role_kwargs(provider, quick_model, deep_model, native):
    graph = _graph(provider, quick_model, deep_model)
    common = graph._get_provider_kwargs()
    quick = graph._get_role_provider_kwargs("quick", common)
    deep = graph._get_role_provider_kwargs("deep", common)
    assert quick[native] == "low"
    assert deep[native] == "high"
    assert quick["temperature"] == deep["temperature"] == 0.2
    assert quick["max_retries"] == deep["max_retries"] == 4
    assert quick[RESOLVED_MARKER] is True
    assert deep[RESOLVED_MARKER] is True


def test_graph_builds_independent_deepseek_effort_kwargs():
    graph = _graph(
        "deepseek",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        quick_effort="high",
        deep_effort="max",
    )
    common = graph._get_provider_kwargs()
    quick = graph._get_role_provider_kwargs("quick", common)
    deep = graph._get_role_provider_kwargs("deep", common)
    assert quick["reasoning_effort"] == "high"
    assert deep["reasoning_effort"] == "max"
    assert quick["temperature"] == deep["temperature"] == 0.2
    assert quick["max_retries"] == deep["max_retries"] == 4
