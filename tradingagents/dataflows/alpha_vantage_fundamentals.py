import json
from datetime import datetime, timezone

from .alpha_vantage_common import _make_api_request
from .lookahead import is_near_live


def _filter_reports_by_date(result, curr_date: str):
    """Drop annual/quarterly reports dated after curr_date to prevent look-ahead.

    ``_make_api_request`` returns the fundamentals payload as a JSON string, so
    parse, filter, and re-serialize. A non-JSON body or an unset ``curr_date`` is
    returned unchanged.
    """
    if not curr_date or not isinstance(result, str):
        return result
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return result
    if not isinstance(payload, dict):
        return result
    for key in ("annualReports", "quarterlyReports"):
        if isinstance(payload.get(key), list):
            payload[key] = [
                r for r in payload[key]
                if r.get("fiscalDateEnding", "") <= curr_date
            ]
    return json.dumps(payload)


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd

    Returns:
        str: Company overview data including financial ratios and key metrics
    """
    if curr_date and not is_near_live(curr_date, ticker):
        return (
            "LIVE_DATA_UNAVAILABLE: Alpha Vantage OVERVIEW is a current "
            "snapshot, not point-in-time historical data. "
            f"Requested analysis date: {curr_date}. The vendor was not queried; "
            "use the dated financial-statement tools for historical evidence."
        )

    result = _make_api_request("OVERVIEW", {"symbol": ticker})
    if not isinstance(result, str):
        result = json.dumps(result)
    requested_date = curr_date or "not supplied (live retrieval compatibility)"
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        f"Alpha Vantage OVERVIEW for {ticker}\n"
        f"Requested analysis date: {requested_date}\n"
        f"Retrieval timestamp: {retrieved_at}\n"
        "Data timing: current snapshot; not point-in-time historical data.\n\n"
        f"{result}"
    )


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve balance sheet data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("BALANCE_SHEET", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve cash flow statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("CASH_FLOW", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date)


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None):
    """Retrieve income statement data for a given ticker symbol using Alpha Vantage."""
    result = _make_api_request("INCOME_STATEMENT", {"symbol": ticker})
    return _filter_reports_by_date(result, curr_date)
