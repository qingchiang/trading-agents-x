from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.agents.utils.runtime import (
    AnalysisToolRuntime,
    tool_runtime_scope,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.lookahead import lookback_start_date

# Inclusive [end - 89 days, end] baseline: exactly 90 calendar dates. A longer
# configured recent window remains authoritative, so extended never shortens it.
# Keep this graph policy separate from the public get_news date-range contract.
EXTENDED_TICKER_NEWS_LOOKBACK_DAYS = 89


@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
    *,
    information_frontier: Annotated[
        str | None,
        InjectedState("information_frontier"),
    ],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    route_kwargs = {"_provenance": True}
    if information_frontier is not None:
        route_kwargs["information_frontier"] = information_frontier
    return route_to_vendor(
        "get_news",
        ticker,
        start_date,
        end_date,
        **route_kwargs,
    )


@tool("get_news")
def get_news_for_analysis(
    ticker: Annotated[str, "Ticker symbol"],
    end_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    window: Annotated[
        Literal["recent", "extended"],
        "Use 'recent' first; use 'extended' only to investigate an older catalyst",
    ] = "recent",
) -> str:
    """Retrieve recent or at-least-90-date news ending on the analysis date."""
    with tool_runtime_scope(runtime, end_date) as cutoff:
        configured_lookback = get_config()["ticker_news_lookback_days"]
        recent_start_date = lookback_start_date(cutoff, configured_lookback)
        baseline_extended_start_date = lookback_start_date(
            cutoff,
            EXTENDED_TICKER_NEWS_LOOKBACK_DAYS,
        )
        # Preserve the configured recent-window contract even when it is already
        # longer than the 90-date baseline. Extended must contain recent, never
        # silently shorten a user-configured range.
        start_date = (
            min(recent_start_date, baseline_extended_start_date)
            if window == "extended"
            else recent_start_date
        )
        route_kwargs = {"_provenance": True}
        information_frontier = getattr(
            runtime.context,
            "information_frontier",
            None,
        )
        if information_frontier is not None:
            route_kwargs["information_frontier"] = information_frontier.isoformat()
        return route_to_vendor(
            "get_news",
            ticker,
            start_date,
            cutoff,
            **route_kwargs,
        )

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int | None, "Days to look back; omit to use the configured default"] = None,
    limit: Annotated[int | None, "Max articles to return; omit to use the configured default"] = None,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor. Defaults for look_back_days and
    limit come from DEFAULT_CONFIG (global_news_lookback_days,
    global_news_article_limit); pass explicit values to override.

    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back; omit to inherit config
        limit (int): Maximum number of articles to return; omit to inherit config

    Returns:
        str: A formatted string containing global news data
    """
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)


@tool("get_global_news")
def get_global_news_for_analysis(
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    look_back_days: Annotated[
        int | None, "Days to look back; omit to use the configured default"
    ] = None,
    limit: Annotated[
        int | None, "Max articles to return; omit to use the configured default"
    ] = None,
) -> str:
    """Retrieve global news ending on the workflow's immutable analysis date."""
    with tool_runtime_scope(runtime, curr_date) as cutoff:
        return route_to_vendor(
            "get_global_news",
            cutoff,
            look_back_days,
            limit,
            _provenance=True,
        )


@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)
