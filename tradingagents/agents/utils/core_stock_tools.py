from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.agents.utils.runtime import (
    AnalysisToolRuntime,
    tool_runtime_scope,
)
from tradingagents.application.evidence_workset import (
    EvidenceToolArtifact,
    build_market_data_artifact,
)
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve stock price data (OHLCV) for a given ticker symbol.
    Uses the configured core_stock_apis vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted dataframe containing the stock price data for the specified ticker symbol in the specified date range.
    """
    return route_to_vendor("get_stock_data", symbol, start_date, end_date)


@tool("get_stock_data", response_format="content_and_artifact")
def get_stock_data_for_analysis(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve OHLCV while keeping the complete table out of model context."""
    with tool_runtime_scope(runtime, end_date) as cutoff:
        raw = route_to_vendor(
            "get_stock_data", symbol, start_date, cutoff, _provenance=True
        )
    return build_market_data_artifact(
        raw,
        symbol=symbol,
        start_date=start_date,
        end_date=cutoff,
    )
