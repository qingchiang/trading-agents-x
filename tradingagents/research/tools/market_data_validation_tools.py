from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.evidence_workset import EvidenceToolArtifact, data_tool_output
from tradingagents.data.interface import route_to_vendor
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


@tool("get_verified_market_snapshot", response_format="content_and_artifact")
def get_verified_market_snapshot(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    look_back_days: Annotated[
        int, "number of recent trading rows to include for sanity-checking"
    ] = 30,
) -> tuple[str, EvidenceToolArtifact]:
    """Build a verified snapshot at the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    return data_tool_output(
        route_to_vendor(
            "get_verified_market_snapshot",
            symbol,
            cutoff,
            look_back_days,
            data_context=runtime.context.data_context,
        )
    )
