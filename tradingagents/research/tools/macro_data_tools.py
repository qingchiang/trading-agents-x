from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.interface import route_to_vendor
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


@tool("get_macro_indicators")
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
) -> str:
    """Retrieve a macro series ending on the immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    return route_to_vendor(
        "get_macro_indicators",
        indicator,
        cutoff,
        look_back_days,
        _provenance=True,
        data_context=runtime.context.data_context,
    )
