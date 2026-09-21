from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.evidence_workset import EvidenceToolArtifact, data_tool_output
from tradingagents.data.interface import route_to_vendor
from tradingagents.domain.data_result import DataResult
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


# The workflow supplies each tool's immutable analysis date through ToolNode.
@tool("get_fundamentals", response_format="content_and_artifact")
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve fundamentals using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    cached = (getattr(runtime, "state", None) or {}).get("fundamental_inputs", {})
    if cached.get("ticker") == ticker and cached.get("cutoff") == cutoff:
        response = cached.get("responses", {}).get("get_fundamentals")
        if response is not None:
            return data_tool_output(DataResult.load(response))
    return data_tool_output(
        route_to_vendor(
            "get_fundamentals",
            ticker,
            cutoff,
            data_context=runtime.context.data_context,
        )
    )


@tool("get_balance_sheet", response_format="content_and_artifact")
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve a balance sheet using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    cached = (getattr(runtime, "state", None) or {}).get("fundamental_inputs", {})
    if cached.get("ticker") == ticker and cached.get("cutoff") == cutoff and freq == "quarterly":
        response = cached.get("responses", {}).get("get_balance_sheet")
        if response is not None:
            return data_tool_output(DataResult.load(response))
    return data_tool_output(
        route_to_vendor(
            "get_balance_sheet",
            ticker,
            freq,
            cutoff,
            data_context=runtime.context.data_context,
        )
    )


@tool("get_cashflow", response_format="content_and_artifact")
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve cash flow using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    cached = (getattr(runtime, "state", None) or {}).get("fundamental_inputs", {})
    if cached.get("ticker") == ticker and cached.get("cutoff") == cutoff and freq == "quarterly":
        response = cached.get("responses", {}).get("get_cashflow")
        if response is not None:
            return data_tool_output(DataResult.load(response))
    return data_tool_output(
        route_to_vendor(
            "get_cashflow",
            ticker,
            freq,
            cutoff,
            data_context=runtime.context.data_context,
        )
    )


@tool("get_income_statement", response_format="content_and_artifact")
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve an income statement using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    cached = (getattr(runtime, "state", None) or {}).get("fundamental_inputs", {})
    if cached.get("ticker") == ticker and cached.get("cutoff") == cutoff and freq == "quarterly":
        response = cached.get("responses", {}).get("get_income_statement")
        if response is not None:
            return data_tool_output(DataResult.load(response))
    return data_tool_output(
        route_to_vendor(
            "get_income_statement",
            ticker,
            freq,
            cutoff,
            data_context=runtime.context.data_context,
        )
    )
