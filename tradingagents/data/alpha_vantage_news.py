import json
from datetime import datetime

from tradingagents.data.alpha_vantage_common import _make_api_request, format_datetime_for_api
from tradingagents.data.context import DataRequestContext
from tradingagents.data.news_selection import news_observations
from tradingagents.data.result_metadata import source_metadata
from tradingagents.domain.data_result import DataResult
from tradingagents.domain.news import NewsCandidate


@source_metadata("get_news", "alpha_vantage")
def get_news(ticker, start_date, end_date, *, data_context: DataRequestContext) -> DataResult[str]:
    """Returns live and historical market news & sentiment data from premier news outlets worldwide.

    Covers stocks, cryptocurrencies, forex, and topics like fiscal policy, mergers & acquisitions, IPOs.

    Args:
        ticker: Stock symbol for news articles.
        start_date: Start date for news search.
        end_date: End date for news search.

    Returns:
        Dictionary containing news sentiment data or JSON string.
    """

    params = {
        "tickers": ticker,
        "time_from": format_datetime_for_api(start_date),
        "time_to": format_datetime_for_api(end_date),
    }

    return _news_result(_make_api_request("NEWS_SENTIMENT", params), ticker)


@source_metadata("get_global_news", "alpha_vantage")
def get_global_news(
    curr_date, look_back_days: int = 7, limit: int = 50, *, data_context: DataRequestContext
) -> DataResult[str]:
    """Returns global market news & sentiment data without ticker-specific filtering.

    Covers broad market topics like financial markets, economy, and more.

    Args:
        curr_date: Current date in yyyy-mm-dd format.
        look_back_days: Number of days to look back (default 7).
        limit: Maximum number of articles (default 50).

    Returns:
        Dictionary containing global news sentiment data or JSON string.
    """
    from datetime import datetime, timedelta

    # Calculate start date
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - timedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    params = {
        "topics": "financial_markets,economy_macro,economy_monetary",
        "time_from": format_datetime_for_api(start_date),
        "time_to": format_datetime_for_api(curr_date),
        "limit": str(limit),
    }

    return _news_result(_make_api_request("NEWS_SENTIMENT", params), "", global_news=True)


@source_metadata("get_insider_transactions", "alpha_vantage")
def get_insider_transactions(symbol: str, *, data_context: DataRequestContext) -> DataResult[str]:
    """Returns latest and historical insider transactions by key stakeholders.

    Covers transactions by founders, executives, board members, etc.

    Args:
        symbol: Ticker symbol. Example: "IBM".

    Returns:
        Dictionary containing insider transaction data or JSON string.
    """

    params = {
        "symbol": symbol,
    }

    return DataResult(_make_api_request("INSIDER_TRANSACTIONS", params))


def _news_result(content: str, ticker: str, *, global_news: bool = False) -> DataResult[str]:
    payload = json.loads(content)
    rows = []
    for article in payload.get("feed", []):
        published = article.get("time_published")
        try:
            published = datetime.strptime(published, "%Y%m%dT%H%M%S").isoformat() + "+00:00"
        except (TypeError, ValueError):
            published = None
        rows.append(
            NewsCandidate(
                "alpha_vantage",
                article.get("title", ""),
                json.dumps(article, ensure_ascii=False),
                published,
                article.get("url"),
            )
        )
    return DataResult(
        content,
        news=tuple(rows),
        observations=news_observations(rows, "alpha_vantage", ticker, global_news=global_news),
    )
