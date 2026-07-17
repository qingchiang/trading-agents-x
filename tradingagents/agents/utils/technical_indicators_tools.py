from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.interface import route_to_vendor


def _get_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
    *,
    provenance: bool = False,
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
                )
            )
        except ValueError as exc:
            results.append(str(exc))
    return "\n\n".join(results)


@tool
def get_indicators(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """
    Retrieve a single technical indicator for a given ticker symbol.
    Uses the configured technical_indicators vendor.
    Args:
        symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
        indicator (str): A single technical indicator name, e.g. 'rsi', 'macd'. Call this tool once per indicator.
        curr_date (str): The current trading date you are trading on, YYYY-mm-dd
        look_back_days (int): How many days to look back, default is 30
    Returns:
        str: A formatted dataframe containing the technical indicators for the specified ticker symbol and indicator.
    """
    return _get_indicators(symbol, indicator, curr_date, look_back_days)


@tool("get_indicators")
def get_indicators_for_analysis(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to retrieve"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """Retrieve indicators using the workflow's immutable analysis date."""
    return _get_indicators(
        symbol, indicator, curr_date, look_back_days, provenance=True
    )
