from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.lookahead import is_live


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
    limit: Annotated[int | None, "Max markets to return; omit for 6"] = None,
) -> str:
    """Retrieve a live prediction-market snapshot only for near-live analysis."""
    if not is_live(curr_date):
        return (
            "LIVE_DATA_UNAVAILABLE: prediction markets expose a current snapshot, "
            f"not point-in-time history; historical analysis date {curr_date} was "
            "not requested from the vendor."
        )
    return route_to_vendor("get_prediction_markets", topic, limit)
