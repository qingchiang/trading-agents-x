from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.data.context import DataRequestContext
from tradingagents.data.interface import route_to_vendor
from tradingagents.research.tools.runtime import AnalysisToolRuntime, analysis_cutoff


def _get_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
    *,
    provenance: bool = False,
    data_context: DataRequestContext,
) -> str:
    """Route one or more comma-separated indicators with one trusted date."""
    indicators = [i.strip().lower() for i in indicator.split(",") if i.strip()]
    results = []
    for ind in indicators:
        try:
            results.append(
                route_to_vendor(
                    "get_indicators",
                    symbol,
                    ind,
                    curr_date,
                    look_back_days,
                    _provenance=provenance,
                    data_context=data_context,
                )
            )
        except ValueError as exc:
            results.append(str(exc))
    return "\n\n".join(results)


@tool("get_indicators")
def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to retrieve"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    runtime: AnalysisToolRuntime,
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """Retrieve indicators using the workflow's immutable analysis date."""
    cutoff = analysis_cutoff(runtime, curr_date)
    return _get_indicators(
        symbol,
        indicator,
        cutoff,
        look_back_days,
        provenance=True,
        data_context=runtime.context.data_context,
    )
