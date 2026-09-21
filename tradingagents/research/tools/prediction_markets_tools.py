from datetime import UTC, datetime
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.evidence_workset import EvidenceToolArtifact, data_tool_output
from tradingagents.data.interface import route_to_vendor
from tradingagents.data.lookahead import is_near_live
from tradingagents.domain.data import ProvenanceRecord
from tradingagents.domain.data_result import DataResult
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


@tool("get_prediction_markets", response_format="content_and_artifact")
def get_prediction_markets(
    topic: Annotated[
        str,
        "Event topic/keyword, e.g. 'Fed rate cut' or 'recession 2026'.",
    ],
    ticker: Annotated[str, InjectedState("company_of_interest")],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    limit: Annotated[int | None, "Max markets to return; omit for 6"] = None,
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve a live prediction-market snapshot only for near-live analysis."""
    cutoff = analysis_cutoff(runtime, curr_date)
    if not is_near_live(cutoff, ticker):
        return data_tool_output(
            DataResult(
                "LIVE_DATA_UNAVAILABLE: prediction markets expose a current snapshot, "
                f"not point-in-time history; historical analysis date {cutoff} was "
                "not requested from the vendor.",
            )
            .with_provenance(
                ProvenanceRecord(
                    evidence="get_prediction_markets",
                    source="Polymarket",
                    requested=cutoff,
                    effective="—",
                    timing="live-only; unavailable for historical or future date; vendor not queried",
                )
            )
            .with_scope("live_only")
        )
    result = route_to_vendor(
        "get_prediction_markets", topic, limit, data_context=runtime.context.data_context
    )
    retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
    unavailable = (
        "DATA_UNAVAILABLE" in result.content or "currently unavailable" in result.content.casefold()
    )
    return data_tool_output(
        result.with_provenance(
            ProvenanceRecord(
                evidence="get_prediction_markets",
                source="Polymarket",
                requested=cutoff,
                effective="—" if unavailable else "retrieval-time open markets",
                timing="live-only retrieval unavailable"
                if unavailable
                else "live non-point-in-time",
                retrieved_at=retrieved_at,
            )
        ).with_scope("live_only")
    )
