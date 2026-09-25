from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.evidence_workset import EvidenceToolArtifact, build_market_data_artifact
from tradingagents.data.interface import route_to_vendor
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


@tool("get_stock_data", response_format="content_and_artifact")
def get_stock_data(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
) -> tuple[str, EvidenceToolArtifact]:
    """Retrieve OHLCV while keeping the complete table out of model context."""
    cutoff = analysis_cutoff(runtime, end_date)
    raw = route_to_vendor(
        "get_stock_data",
        symbol,
        start_date,
        cutoff,
        data_context=runtime.context.data_context,
    )
    return build_market_data_artifact(
        raw,
        symbol=symbol,
        start_date=start_date,
        end_date=cutoff,
    )
