"""Guard the fundamentals analyst's point-in-time interpretation boundary."""

from typing import TypedDict
from unittest import mock

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

import tradingagents.research.analysts.fundamentals_analyst as fa
from tests.factories import analyst_runtime
from tradingagents.research.tools.fundamental_data_tools import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)


def test_full_prefetches_financial_core_and_reuses_it_on_next_model_call(monkeypatch):
    from langchain_core.runnables import RunnableLambda

    calls = []
    prompts = []

    def route(method, *args, **kwargs):
        calls.append(method)
        return f"core data {method}"

    class Model:
        def bind_tools(self, tools):
            return RunnableLambda(lambda prompt: prompts.append(prompt.to_string()) or AIMessage(content="report"))

    monkeypatch.setattr(fa, "route_to_vendor", route)
    node = fa.create_fundamentals_analyst(Model())
    state = {"company_of_interest": "GOOG", "trade_date": "2026-09-05", "messages": []}
    first = node(state, analyst_runtime())
    node({**state, **first}, analyst_runtime())
    assert len(calls) == 4
    assert "core data get_cashflow" in prompts[0]


@pytest.mark.unit
def test_fundamentals_prompt_preserves_missing_and_historical_data_boundaries(monkeypatch):
    from tests.factories import captured_analyst_prompt

    source = captured_analyst_prompt(monkeypatch, "fundamentals")
    assert "missing or unprovided financial fields as unknown, never as zero" in source
    assert "not point-in-time historical data" in source
    assert "must not be presented as evidence" in source
    assert "Do not substitute EBIT, pretax income" in source
    assert "workflow injects the exact analysis date" in source
    assert "do not attempt to supply or override" in source


@pytest.mark.unit
def test_analysis_tool_schemas_hide_injected_date_from_the_llm():
    for analysis_tool in (
        get_fundamentals,
        get_balance_sheet,
        get_cashflow,
        get_income_statement,
    ):
        schema = analysis_tool.tool_call_schema.model_json_schema()
        assert "curr_date" not in schema["properties"]


@pytest.mark.unit
def test_tool_node_injects_trade_date_into_fundamental_vendor_call():
    class ToolState(TypedDict):
        messages: list
        trade_date: str

    workflow = StateGraph(ToolState)
    workflow.add_node("tools", ToolNode([get_balance_sheet]))
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
        "tradingagents.research.tools.fundamental_data_tools.route_to_vendor",
        return_value="SAFE",
    ) as router:
        result = graph.invoke(state, context=analyst_runtime(analysis_date=state["trade_date"]).context)

    router.assert_called_once_with(
        "get_balance_sheet",
        "9984.T",
        "annual",
        "2020-01-15",
        _provenance=True,
        data_context=analyst_runtime(analysis_date=state["trade_date"]).context.data_context,
    )
    assert result["messages"][0].content == "SAFE"
