from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.interface import route_to_vendor
from tradingagents.research.tools.runtime import AnalysisToolRuntime, tool_runtime_scope


@tool("get_verified_market_snapshot")
def get_verified_market_snapshot(
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
