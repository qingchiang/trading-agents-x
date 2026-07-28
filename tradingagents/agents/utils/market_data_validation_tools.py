from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.agents.utils.runtime import (
    AnalysisToolRuntime,
    tool_runtime_scope,
)
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_verified_market_snapshot(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "the current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[
        int, "number of recent trading rows to include for sanity-checking"
    ] = 30,
) -> str:
    """Deterministic verification snapshot for exact market-data claims.

    Returns the latest OHLCV row on or before curr_date, common technical
    indicators, and recent closes. Call this before making exact claims about
    price levels, Bollinger bands, RSI, MACD, moving averages, support /
    resistance, or historical comparisons, and treat it as the source of truth.
    """
    return route_to_vendor(
        "get_verified_market_snapshot", symbol, curr_date, look_back_days
    )


@tool("get_verified_market_snapshot")
def get_verified_market_snapshot_for_analysis(
    symbol: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    look_back_days: Annotated[
        int, "number of recent trading rows to include for sanity-checking"
    ] = 30,
) -> str:
    """Build a verified snapshot at the workflow's immutable analysis date."""
    with tool_runtime_scope(runtime, curr_date) as cutoff:
        return route_to_vendor(
            "get_verified_market_snapshot",
            symbol,
            cutoff,
            look_back_days,
            _provenance=True,
        )
