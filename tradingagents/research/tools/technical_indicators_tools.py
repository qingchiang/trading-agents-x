from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.context import DataRequestContext
from tradingagents.data.evidence_workset import EvidenceToolArtifact, data_tool_output
from tradingagents.data.interface import route_to_vendor
from tradingagents.domain.data_result import DataResult
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


def _get_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
    *,
    data_context: DataRequestContext,
) -> tuple[str, EvidenceToolArtifact]:
    """Route one or more comma-separated indicators with one trusted date."""
    indicators = [i.strip().lower() for i in indicator.split(",") if i.strip()]
    results = []
    for ind in indicators:
        try:
            results.append(
                route_to_vendor(
                    "get_indicators",
                    symbol,
                    ind,
                    curr_date,
                    look_back_days,
                    data_context=data_context,
                )
            )
        except ValueError as exc:
            results.append(DataResult(str(exc)))
    return DataResult.combine(results)


@tool("get_indicators", response_format="content_and_artifact")
def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to retrieve"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve indicators using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    return data_tool_output(
        _get_indicators(
            symbol,
            indicator,
            cutoff,
            look_back_days,
            data_context=runtime.context.data_context,
        )
    )
