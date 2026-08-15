"""Graph tools must use AgentState.trade_date instead of model-supplied dates."""

import warnings
from datetime import datetime
from typing import get_type_hints
from unittest import mock

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

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
from tradingagents.application.anchor_readiness import AnchorReadinessResult
from tradingagents.application.contracts import AnalysisRequest, MemoryContext
from tradingagents.application.runtime import RunContext
from tradingagents.provenance import extract_provenance, strip_provenance_markers


class _ToolState(TypedDict):
    messages: list
    trade_date: str
    company_of_interest: str


@pytest.mark.unit
def test_run_context_runtime_annotations_are_resolvable():
    hints = get_type_hints(RunContext)

    assert hints["anchor_readiness"] == AnchorReadinessResult | None


def _invoke_tool(tool, args, trade_date="2020-01-15", context=None):
    workflow = StateGraph(
        _ToolState,
        **({"context_schema": RunContext} if context is not None else {}),
    )
    workflow.add_node("tools", ToolNode([tool]))
    workflow.add_edge(START, "tools")
    workflow.add_edge("tools", END)
    graph = workflow.compile()
    return graph.invoke(
        {
            "trade_date": trade_date,
            "company_of_interest": "NVDA",
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
        },
        **({"context": context} if context is not None else {}),
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
        "get_stock_data",
        "NVDA",
        "2019-12-01",
        "2020-01-15",
        _provenance=True,
    )
    message = result["messages"][0]
    assert "Market data analytical overview" in message.content
    assert "SAFE" not in message.content
    assert message.artifact["source_content"] == "SAFE"


@pytest.mark.unit
def test_tool_node_accepts_typed_run_context_without_serialization_warning(
    app_settings,
):
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2020-01-15",
    )
    settings = app_settings.resolve_run(request)
    context = RunContext(
        run_id="typed-tool-runtime",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=MemoryContext(
            instrument="NVDA",
            market="America/New_York",
        ),
        instrument_context="The instrument is NVDA.",
        cancel_requested=lambda: False,
    )

    with (
        mock.patch(
            "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
            return_value="SAFE",
        ) as router,
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        result = _invoke_tool(
            get_stock_data_for_analysis,
            {"symbol": "NVDA", "start_date": "2019-12-01"},
            context=context,
        )

    router.assert_called_once_with(
        "get_stock_data",
        "NVDA",
        "2019-12-01",
        "2020-01-15",
        _provenance=True,
    )
    message = result["messages"][0]
    assert "Market data analytical overview" in message.content
    assert "SAFE" not in message.content
    assert message.artifact["source_content"] == "SAFE"
    assert not any(
        "PydanticSerializationUnexpectedValue" in str(item.message)
        or "Expected `none`" in str(item.message)
        for item in caught
    )


@pytest.mark.unit
def test_verified_market_snapshot_forwards_frozen_information_frontier(
    app_settings,
):
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        analysts=("market",),
    )
    settings = app_settings.resolve_run(request)
    frontier = datetime.fromisoformat("2026-08-10T23:59:00+09:00")
    context = RunContext(
        run_id="market-snapshot-frontier",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
        instrument_context="The instrument is 4568.T.",
        cancel_requested=lambda: False,
        information_frontier=frontier,
    )

    with mock.patch(
        "tradingagents.agents.utils.market_data_validation_tools.route_to_vendor",
        return_value="SAFE",
    ) as router:
        _invoke_tool(
            get_verified_market_snapshot_for_analysis,
            {"symbol": request.ticker},
            trade_date=request.analysis_date.isoformat(),
            context=context,
        )

    router.assert_called_once_with(
        "get_verified_market_snapshot",
        "4568.T",
        "2026-08-10",
        30,
        _provenance=True,
        information_frontier=frontier.isoformat(),
    )


@pytest.mark.unit
def test_stock_data_forwards_frozen_information_frontier(app_settings):
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        analysts=("market",),
    )
    settings = app_settings.resolve_run(request)
    frontier = datetime.fromisoformat("2026-08-10T23:59:00+09:00")
    context = RunContext(
        run_id="market-table-frontier",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
        instrument_context="The instrument is 4568.T.",
        cancel_requested=lambda: False,
        information_frontier=frontier,
    )

    with mock.patch(
        "tradingagents.agents.utils.core_stock_tools.route_to_vendor",
        return_value="SAFE",
    ) as router:
        _invoke_tool(
            get_stock_data_for_analysis,
            {"symbol": request.ticker, "start_date": "2026-05-13"},
            trade_date=request.analysis_date.isoformat(),
            context=context,
        )

    router.assert_called_once_with(
        "get_stock_data",
        "4568.T",
        "2026-05-13",
        "2026-08-10",
        _provenance=True,
        information_frontier=frontier.isoformat(),
    )


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
        "get_news",
        "9984.T",
        "2020-01-01",
        "2020-01-15",
        _provenance=True,
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
        "get_news",
        "9984.T",
        "2019-10-18",
        "2020-01-15",
        _provenance=True,
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

    expected = mock.call(
        "get_news",
        "9984.T",
        "2019-09-17",
        "2020-01-15",
        _provenance=True,
    )
    assert router.call_args_list == [expected, expected]


@pytest.mark.unit
def test_prediction_market_gate_skips_historical_vendor_call(monkeypatch):
    clock = mock.Mock()
    retrieved = mock.Mock()
    retrieved.isoformat.return_value = "2026-07-17T01:02:03+00:00"
    clock.now.return_value = retrieved

    def live_result(*_args):
        assert not clock.now.called
        return "LIVE"

    router = mock.Mock(side_effect=live_result)
    monkeypatch.setattr(
        "tradingagents.agents.utils.prediction_markets_tools.route_to_vendor",
        router,
    )
    monkeypatch.setattr(
        "tradingagents.agents.utils.prediction_markets_tools.datetime",
        clock,
    )

    historical = _invoke_tool(
        get_prediction_markets_for_analysis,
        {"topic": "Fed rate cut", "limit": 3},
    )

    router.assert_not_called()
    assert "LIVE_DATA_UNAVAILABLE" in historical["messages"][0].content

    monkeypatch.setattr(
        "tradingagents.agents.utils.prediction_markets_tools.is_near_live",
        lambda curr_date, ticker: True,
    )
    live = _invoke_tool(
        get_prediction_markets_for_analysis,
        {"topic": "Fed rate cut", "limit": 3},
        trade_date="2026-07-17",
    )
    router.assert_called_once_with("get_prediction_markets", "Fed rate cut", 3)
    content = live["messages"][0].content
    assert strip_provenance_markers(content) == "LIVE"
    record = extract_provenance(content)[0]
    assert record.source == "Polymarket"
    assert record.requested == "2026-07-17"
    assert record.timing == "live non-point-in-time"
    assert record.retrieved_at == "2026-07-17T01:02:03+00:00"
