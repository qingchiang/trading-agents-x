from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.agents.utils.runtime import (
    AnalysisToolRuntime,
    tool_runtime_scope,
)
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_macro_indicators(
    indicator: Annotated[
        str,
        "Macro indicator. US (FRED): 'cpi', 'core_pce', 'unemployment', "
        "'fed_funds_rate', '10y_treasury', 'yield_curve', 'real_gdp', 'vix', or a "
        "raw FRED series ID such as 'CPIAUCSL'. Japan (official sources): 'jp_cpi', "
        "'jp_core_cpi' (e-Stat), 'jp_policy_rate', 'jp_tankan' (BOJ).",
    ],
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format; the end of the window"],
    look_back_days: Annotated[
        int | None, "Trailing window length in days; omit for a 1-year window"
    ] = None,
) -> str:
    """
    Retrieve a macroeconomic indicator time series: US series from FRED (policy
    rates, Treasury yields, inflation, labor, growth) and Japanese series from the
    official sources (e-Stat CPI, BOJ policy rate / Tankan). The indicator is
    routed to whichever vendor serves it. Returns the series title, units,
    frequency, the latest value, the change over the window, and a recent
    observation table.

    Args:
        indicator (str): Friendly alias (US or Japan) or raw FRED series ID
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Trailing window length; omit for a 1-year window

    Returns:
        str: A formatted markdown report of the macro series
    """
    return route_to_vendor("get_macro_indicators", indicator, curr_date, look_back_days)


@tool("get_macro_indicators")
def get_macro_indicators_for_analysis(
    indicator: Annotated[
        str,
        "Macro indicator alias or raw FRED series ID.",
    ],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    look_back_days: Annotated[
        int | None, "Trailing window length in days; omit for a 1-year window"
    ] = None,
) -> str:
    """Retrieve a macro series ending on the immutable analysis date."""
    with tool_runtime_scope(runtime, curr_date) as cutoff:
        return route_to_vendor(
            "get_macro_indicators",
            indicator,
            cutoff,
            look_back_days,
            _provenance=True,
        )
