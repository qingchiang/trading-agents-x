from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.evidence_workset import EvidenceToolArtifact, data_tool_output
from tradingagents.data.interface import route_to_vendor
from tradingagents.data.lookahead import lookback_start_date
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff

# Inclusive [end - 89 days, end] baseline: exactly 90 calendar dates. A longer
# configured recent window remains authoritative, so extended never shortens it.
# Keep this graph policy separate from the public get_news date-range contract.
EXTENDED_TICKER_NEWS_LOOKBACK_DAYS = 89


@tool("get_news", response_format="content_and_artifact")
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    end_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    window: Annotated[
        Literal["recent", "extended"],
        "Use 'recent' first; use 'extended' only to investigate an older catalyst",
    ] = "recent",
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve recent or at-least-90-date news ending on the analysis date."""
    cutoff = analysis_cutoff(runtime, end_date)
    configured_lookback = runtime.context.dataflow_config["ticker_news_lookback_days"]
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
    return data_tool_output(
        route_to_vendor(
            "get_news",
            ticker,
            start_date,
            cutoff,
            data_context=runtime.context.data_context,
        )
    )


@tool("get_global_news", response_format="content_and_artifact")
def get_global_news(
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    look_back_days: Annotated[
        int | None, "Days to look back; omit to use the configured default"
    ] = None,
    limit: Annotated[
        int | None, "Max articles to return; omit to use the configured default"
    ] = None,
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve global news ending on the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    return data_tool_output(
        route_to_vendor(
            "get_global_news",
            cutoff,
            look_back_days,
            limit,
            data_context=runtime.context.data_context,
        )
    )
