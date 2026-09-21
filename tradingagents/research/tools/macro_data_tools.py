from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.evidence_workset import EvidenceToolArtifact, data_tool_output
from tradingagents.data.interface import route_to_vendor
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


@tool("get_macro_indicators", response_format="content_and_artifact")
def get_macro_indicators(
    indicator: Annotated[
        str,
        "Macro indicator alias, or a 1-25 character alphanumeric raw FRED series ID.",
    ],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    look_back_days: Annotated[
        int | None, "Trailing window length in days; omit for a 1-year window"
    ] = None,
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve a macro series ending on the immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    return data_tool_output(
        route_to_vendor(
            "get_macro_indicators",
            indicator,
            cutoff,
            look_back_days,
            data_context=runtime.context.data_context,
        )
    )
