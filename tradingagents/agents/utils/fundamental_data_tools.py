from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing comprehensive fundamental data
    """
    return route_to_vendor("get_fundamentals", ticker, curr_date)


@tool
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve balance sheet data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing balance sheet data
    """
    return route_to_vendor("get_balance_sheet", ticker, freq, curr_date)


@tool
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve cash flow statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing cash flow statement data
    """
    return route_to_vendor("get_cashflow", ticker, freq, curr_date)


@tool
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve income statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing income statement data
    """
    return route_to_vendor("get_income_statement", ticker, freq, curr_date)


# Graph-only variants. Their public tool names intentionally match the legacy
# tools above, but ``curr_date`` is hidden from the LLM and injected by ToolNode
# from AgentState.trade_date. Direct/programmatic users keep the original tools
# and their no-date live compatibility.
@tool("get_fundamentals")
def get_fundamentals_for_analysis(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
) -> str:
    """Retrieve fundamentals using the workflow's immutable analysis date."""
    return route_to_vendor("get_fundamentals", ticker, curr_date, _provenance=True)


@tool("get_balance_sheet")
def get_balance_sheet_for_analysis(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> str:
    """Retrieve a balance sheet using the workflow's immutable analysis date."""
    return route_to_vendor(
        "get_balance_sheet", ticker, freq, curr_date, _provenance=True
    )


@tool("get_cashflow")
def get_cashflow_for_analysis(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> str:
    """Retrieve cash flow using the workflow's immutable analysis date."""
    return route_to_vendor("get_cashflow", ticker, freq, curr_date, _provenance=True)


@tool("get_income_statement")
def get_income_statement_for_analysis(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, InjectedState("trade_date")],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
) -> str:
    """Retrieve an income statement using the workflow's immutable analysis date."""
    return route_to_vendor(
        "get_income_statement", ticker, freq, curr_date, _provenance=True
    )
