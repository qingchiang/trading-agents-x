from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.interface import route_to_vendor
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


# The workflow supplies each tool's immutable analysis date through ToolNode.
@tool("get_fundamentals")
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
) -> str:
    """Retrieve fundamentals using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    cached = (getattr(runtime, "state", None) or {}).get("fundamental_inputs", {})
    if cached.get("ticker") == ticker and cached.get("cutoff") == cutoff:
        response = cached.get("responses", {}).get("get_fundamentals")
        if response is not None:
            return response
    return route_to_vendor(
        "get_fundamentals",
        ticker,
        cutoff,
        _provenance=True,
        data_context=runtime.context.data_context,
    )


@tool("get_balance_sheet")
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> str:
    """Retrieve a balance sheet using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    cached = (getattr(runtime, "state", None) or {}).get("fundamental_inputs", {})
    if cached.get("ticker") == ticker and cached.get("cutoff") == cutoff and freq == "quarterly":
        response = cached.get("responses", {}).get("get_balance_sheet")
        if response is not None:
            return response
    return route_to_vendor(
        "get_balance_sheet",
        ticker,
        freq,
        cutoff,
        _provenance=True,
        data_context=runtime.context.data_context,
    )


@tool("get_cashflow")
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> str:
    """Retrieve cash flow using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    cached = (getattr(runtime, "state", None) or {}).get("fundamental_inputs", {})
    if cached.get("ticker") == ticker and cached.get("cutoff") == cutoff and freq == "quarterly":
        response = cached.get("responses", {}).get("get_cashflow")
        if response is not None:
            return response
    return route_to_vendor(
        "get_cashflow",
        ticker,
        freq,
        cutoff,
        _provenance=True,
        data_context=runtime.context.data_context,
    )


@tool("get_income_statement")
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> str:
    """Retrieve an income statement using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    cached = (getattr(runtime, "state", None) or {}).get("fundamental_inputs", {})
    if cached.get("ticker") == ticker and cached.get("cutoff") == cutoff and freq == "quarterly":
        response = cached.get("responses", {}).get("get_income_statement")
        if response is not None:
            return response
    return route_to_vendor(
        "get_income_statement",
        ticker,
        freq,
        cutoff,
        _provenance=True,
        data_context=runtime.context.data_context,
    )
