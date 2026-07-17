"""Graph tools must use AgentState.trade_date instead of model-supplied dates."""

from typing import TypedDict
from unittest import mock

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data_for_analysis,
)
from tradingagents.agents.utils.macro_data_tools import (
    get_macro_indicators_for_analysis,
)
from tradingagents.agents.utils.market_data_validation_tools import (
    get_verified_market_snapshot_for_analysis,
)
from tradingagents.agents.utils.news_data_tools import (
    get_global_news_for_analysis,
    get_news_for_analysis,
)
from tradingagents.agents.utils.prediction_markets_tools import (
    get_prediction_markets_for_analysis,
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators_for_analysis,
)


class _ToolState(TypedDict):
    messages: list
    trade_date: str


def _invoke_tool(tool, args, trade_date="2020-01-15"):
    workflow = StateGraph(_ToolState)
    workflow.add_node("tools", ToolNode([tool]))
    workflow.add_edge(START, "tools")
    workflow.add_edge("tools", END)
    graph = workflow.compile()
    return graph.invoke(
        {
            "trade_date": trade_date,
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": tool.name,
                            "args": args,
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
        }
    )


@pytest.mark.unit
def test_market_and_news_tool_schemas_hide_workflow_dates():
    tools = (
        get_stock_data_for_analysis,
        get_indicators_for_analysis,
        get_verified_market_snapshot_for_analysis,
        get_news_for_analysis,
        get_global_news_for_analysis,
        get_macro_indicators_for_analysis,
        get_prediction_markets_for_analysis,
    )
    for analysis_tool in tools:
        properties = analysis_tool.tool_call_schema.model_json_schema()["properties"]
        assert "curr_date" not in properties
        assert "end_date" not in properties
    assert set(
        get_news_for_analysis.tool_call_schema.model_json_schema()["properties"]
    ) == {"ticker", "window"}


@pytest.mark.unit
def test_market_tool_node_injects_trade_date_as_end_date():
    with mock.patch(
        "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
        return_value="SAFE",
    ) as router:
        result = _invoke_tool(
            get_stock_data_for_analysis,
            {"symbol": "NVDA", "start_date": "2019-12-01"},
        )

    router.assert_called_once_with(
        "get_stock_data", "NVDA", "2019-12-01", "2020-01-15"
    )
    assert result["messages"][0].content == "SAFE"


@pytest.mark.unit
def test_news_tool_node_derives_window_from_injected_trade_date():
    with (
        mock.patch(
            "tradingagents.agents.utils.news_data_tools.get_config",
            return_value={"ticker_news_lookback_days": 14},
        ),
        mock.patch(
            "tradingagents.agents.utils.news_data_tools.route_to_vendor",
            return_value="SAFE",
        ) as router,
    ):
        _invoke_tool(get_news_for_analysis, {"ticker": "9984.T"})

    router.assert_called_once_with(
        "get_news", "9984.T", "2020-01-01", "2020-01-15"
    )


@pytest.mark.unit
def test_news_tool_node_supports_bounded_extended_window():
    with mock.patch(
        "tradingagents.agents.utils.news_data_tools.route_to_vendor",
        return_value="SAFE",
    ) as router:
        _invoke_tool(
            get_news_for_analysis,
            {"ticker": "9984.T", "window": "extended"},
        )

    router.assert_called_once_with(
        "get_news", "9984.T", "2019-10-18", "2020-01-15"
    )


@pytest.mark.unit
def test_news_windows_preserve_a_configured_range_longer_than_90_dates():
    with (
        mock.patch(
            "tradingagents.agents.utils.news_data_tools.get_config",
            return_value={"ticker_news_lookback_days": 120},
        ),
        mock.patch(
            "tradingagents.agents.utils.news_data_tools.route_to_vendor",
            return_value="SAFE",
        ) as router,
    ):
        _invoke_tool(get_news_for_analysis, {"ticker": "9984.T"})
        _invoke_tool(
            get_news_for_analysis,
            {"ticker": "9984.T", "window": "extended"},
        )

    expected = mock.call("get_news", "9984.T", "2019-09-17", "2020-01-15")
    assert router.call_args_list == [expected, expected]


@pytest.mark.unit
def test_prediction_market_gate_skips_historical_vendor_call(monkeypatch):
    router = mock.Mock(return_value="LIVE")
    monkeypatch.setattr(
        "tradingagents.agents.utils.prediction_markets_tools.route_to_vendor",
        router,
    )

    historical = _invoke_tool(
        get_prediction_markets_for_analysis,
        {"topic": "Fed rate cut", "limit": 3},
    )

    router.assert_not_called()
    assert "LIVE_DATA_UNAVAILABLE" in historical["messages"][0].content

    monkeypatch.setattr(
        "tradingagents.agents.utils.prediction_markets_tools.is_live",
        lambda curr_date: True,
    )
    live = _invoke_tool(
        get_prediction_markets_for_analysis,
        {"topic": "Fed rate cut", "limit": 3},
        trade_date="2026-07-17",
    )
    router.assert_called_once_with("get_prediction_markets", "Fed rate cut", 3)
    assert live["messages"][0].content == "LIVE"
