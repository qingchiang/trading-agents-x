from datetime import datetime, timezone
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.agents.utils.runtime import (
    AnalysisToolRuntime,
    tool_runtime_scope,
)
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.lookahead import is_live
from tradingagents.provenance import ProvenanceRecord, attach_provenance


@tool
def get_prediction_markets(
    topic: Annotated[
        str,
        "Event topic/keyword, e.g. 'Fed rate cut', 'recession 2026', "
        "'US election', or a sector/company event.",
    ],
    limit: Annotated[int | None, "Max markets to return; omit for a default of 6"] = None,
) -> str:
    """
    Retrieve live, market-implied probabilities for forward-looking events from
    prediction markets (Polymarket): Fed decisions, recession, elections,
    geopolitics, crypto. Returns the most-traded open markets matching the
    topic, each with its implied probability, traded volume, resolution date,
    and recent move. Uses the configured prediction_markets vendor.

    Args:
        topic (str): Event keyword(s) to search
        limit (int): Max markets to return; omit for a default of 6

    Returns:
        str: A formatted markdown report of matching prediction markets
    """
    return route_to_vendor("get_prediction_markets", topic, limit)


@tool("get_prediction_markets")
def get_prediction_markets_for_analysis(
    topic: Annotated[
        str,
        "Event topic/keyword, e.g. 'Fed rate cut' or 'recession 2026'.",
    ],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    limit: Annotated[int | None, "Max markets to return; omit for 6"] = None,
) -> str:
    """Retrieve a live prediction-market snapshot only for near-live analysis."""
    with tool_runtime_scope(runtime, curr_date) as cutoff:
        if not is_live(cutoff):
            return attach_provenance(
                "LIVE_DATA_UNAVAILABLE: prediction markets expose a current snapshot, "
                f"not point-in-time history; historical analysis date {cutoff} was "
                "not requested from the vendor.",
                ProvenanceRecord(
                    evidence="get_prediction_markets",
                    source="Polymarket",
                    requested=cutoff,
                    effective="—",
                    timing="unavailable for historical date; vendor not queried",
                ),
            )
        result = route_to_vendor("get_prediction_markets", topic, limit)
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        unavailable = (
            "DATA_UNAVAILABLE" in result
            or "currently unavailable" in result.casefold()
        )
        return attach_provenance(
            result,
            ProvenanceRecord(
                evidence="get_prediction_markets",
                source="Polymarket",
                requested=cutoff,
                effective="—" if unavailable else "retrieval-time open markets",
                timing=(
                    "retrieval unavailable"
                    if unavailable
                    else "live non-point-in-time"
                ),
                retrieved_at=retrieved_at,
            ),
        )
