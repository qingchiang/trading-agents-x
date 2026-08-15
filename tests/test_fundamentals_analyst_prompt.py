"""Guard the fundamentals analyst's point-in-time interpretation boundary."""

import inspect
from datetime import datetime
from typing import TypedDict
from unittest import mock

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

import tradingagents.agents.analysts.fundamentals_analyst as fa
from tradingagents.agents.utils.fundamental_data_tools import (
    get_balance_sheet_for_analysis,
    get_cashflow_for_analysis,
    get_fundamentals_for_analysis,
    get_income_statement_for_analysis,
)
from tradingagents.application.contracts import AnalysisRequest, MemoryContext
from tradingagents.application.runtime import RunContext


@pytest.mark.unit
def test_fundamentals_prompt_preserves_missing_and_historical_data_boundaries():
    source = inspect.getsource(fa)
    assert "missing or unprovided financial fields as unknown, never as zero" in source
    assert "not point-in-time historical data" in source
    assert "must not be presented as evidence" in source
    assert "Do not substitute EBIT, pretax income" in source
    assert "workflow injects the exact analysis date" in source
    assert "do not attempt to supply or override" in source


@pytest.mark.unit
def test_analysis_tool_schemas_hide_injected_date_from_the_llm():
    for analysis_tool in (
        get_fundamentals_for_analysis,
        get_balance_sheet_for_analysis,
        get_cashflow_for_analysis,
        get_income_statement_for_analysis,
    ):
        schema = analysis_tool.tool_call_schema.model_json_schema()
        assert "curr_date" not in schema["properties"]


@pytest.mark.unit
def test_tool_node_injects_trade_date_into_fundamental_vendor_call():
    class ToolState(TypedDict):
        messages: list
        trade_date: str

    workflow = StateGraph(ToolState)
    workflow.add_node("tools", ToolNode([get_balance_sheet_for_analysis]))
    workflow.add_edge(START, "tools")
    workflow.add_edge("tools", END)
    graph = workflow.compile()
    state = {
        "trade_date": "2020-01-15",
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "get_balance_sheet",
                    "args": {"ticker": "9984.T", "freq": "annual"},
                    "id": "call-1",
                    "type": "tool_call",
                }],
            )
        ],
    }

    with mock.patch(
        "tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
        return_value="SAFE",
    ) as router:
        result = graph.invoke(state)

    router.assert_called_once_with(
        "get_balance_sheet",
        "9984.T",
        "annual",
        "2020-01-15",
        _provenance=True,
    )
    assert result["messages"][0].content == "SAFE"


@pytest.mark.unit
def test_fundamentals_tool_forwards_frozen_information_frontier(app_settings):
    class ToolState(TypedDict):
        messages: list
        trade_date: str

    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        analysts=("fundamentals",),
    )
    settings = app_settings.resolve_run(request)
    frontier = datetime.fromisoformat("2026-08-10T23:59:00+09:00")
    context = RunContext(
        run_id="fundamentals-frontier",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
        instrument_context="The instrument is 4568.T.",
        cancel_requested=lambda: False,
        information_frontier=frontier,
    )
    workflow = StateGraph(ToolState, context_schema=RunContext)
    workflow.add_node("tools", ToolNode([get_fundamentals_for_analysis]))
    workflow.add_edge(START, "tools")
    workflow.add_edge("tools", END)
    graph = workflow.compile()
    state = {
        "trade_date": request.analysis_date.isoformat(),
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_fundamentals",
                        "args": {"ticker": request.ticker},
                        "id": "call-frontier",
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    with mock.patch(
        "tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
        return_value="SAFE",
    ) as router:
        graph.invoke(state, context=context)

    router.assert_called_once_with(
        "get_fundamentals",
        "4568.T",
        "2026-08-10",
        _provenance=True,
        information_frontier=frontier.isoformat(),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("analysis_tool", "tool_name"),
    (
        (get_balance_sheet_for_analysis, "get_balance_sheet"),
        (get_cashflow_for_analysis, "get_cashflow"),
        (get_income_statement_for_analysis, "get_income_statement"),
    ),
)
def test_statement_tools_forward_frozen_information_frontier(
    app_settings,
    analysis_tool,
    tool_name,
):
    class ToolState(TypedDict):
        messages: list
        trade_date: str

    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        analysts=("fundamentals",),
    )
    settings = app_settings.resolve_run(request)
    frontier = datetime.fromisoformat("2026-08-10T23:59:00+09:00")
    context = RunContext(
        run_id=f"{tool_name}-frontier",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
        instrument_context="The instrument is 4568.T.",
        cancel_requested=lambda: False,
        information_frontier=frontier,
    )
    workflow = StateGraph(ToolState, context_schema=RunContext)
    workflow.add_node("tools", ToolNode([analysis_tool]))
    workflow.add_edge(START, "tools")
    workflow.add_edge("tools", END)
    graph = workflow.compile()
    state = {
        "trade_date": request.analysis_date.isoformat(),
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": tool_name,
                        "args": {"ticker": request.ticker, "freq": "quarterly"},
                        "id": f"call-{tool_name}",
                        "type": "tool_call",
                    }
                ],
            )
        ],
    }

    with mock.patch(
        "tradingagents.agents.utils.fundamental_data_tools.route_to_vendor",
        return_value="SAFE",
    ) as router:
        graph.invoke(state, context=context)

    router.assert_called_once_with(
        tool_name,
        "4568.T",
        "quarterly",
        "2026-08-10",
        _provenance=True,
        information_frontier=frontier.isoformat(),
    )
