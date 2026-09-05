"""yfinance-based news data fetching functions."""

import contextlib
from datetime import UTC, date, datetime

import yfinance as yf
from dateutil.relativedelta import relativedelta
from yfinance.exceptions import YFRateLimitError

from .config import get_config
from .errors import VendorRateLimitError
from .instrument_identity import identity_names, resolve_search_identity
from .news_quality import (
    build_company_aliases,
    canonical_headline,
    classify_yahoo_article,
)
from .stockstats_utils import yf_retry
from .symbol_utils import market_timezone, normalize_symbol


def _is_rate_limit(exc: Exception) -> bool:
    return (
        isinstance(exc, YFRateLimitError)
        or getattr(exc, "code", None) == 429
        or getattr(getattr(exc, "response", None), "status_code", None) == 429
        or "429" in str(exc)
        or "rate limit" in str(exc).casefold()
        or "too many requests" in str(exc).casefold()
    )


def _extract_article_data(article: dict) -> dict:
    """Extract article data from yfinance news format (handles nested 'content' structure)."""
    # Handle nested content structure
    if "content" in article:
        content = article["content"]
        title = content.get("title", "No title")
        summary = content.get("summary", "")
        provider = content.get("provider", {})
        publisher = provider.get("displayName", "Unknown")

        # Get URL from canonicalUrl or clickThroughUrl
        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        link = url_obj.get("url", "")

        # Get publish date
        pub_date_str = content.get("pubDate", "")
        pub_date: datetime | date | None = None
        if pub_date_str:
            with contextlib.suppress(ValueError, AttributeError):
                if "T" in pub_date_str or " " in pub_date_str:
                    parsed = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                    pub_date = (
                        parsed if parsed.tzinfo is not None else parsed.date()
                    )
                else:
                    pub_date = date.fromisoformat(pub_date_str)

        return {
            "title": title,
            "summary": summary,
            "publisher": publisher,
            "link": link,
            "pub_date": pub_date,
        }
    else:
        # Fallback for flat structure. Parse the epoch publish time so flat
        # articles are date-filterable too (otherwise they bypass the
        # historical window and leak future news, #992/#1007).
        pub_date: datetime | date | None = None
        ts = article.get("providerPublishTime")
        if ts:
            # Epoch seconds are UTC; parse them as UTC-aware so filtering does
            # not shift with the host timezone (#1126).
            with contextlib.suppress(ValueError, OSError, TypeError):
                pub_date = datetime.fromtimestamp(ts, tz=UTC)
        return {
            "title": article.get("title", "No title"),
            "summary": article.get("summary", ""),
            "publisher": article.get("publisher", "Unknown"),
            "link": article.get("link", ""),
            "pub_date": pub_date,
        }


def _in_news_window(
    pub_date,
    start_dt,
    end_dt,
    *,
    ticker: str | None = None,
) -> bool:
    """Whether an article belongs in the [start_dt, end_dt] window.

    Dated articles are converted from their timestamp timezone to the asset's
    market calendar before the inclusive date comparison. An undated article is
    kept only when the window reaches the present (live run) — in a historical
    window it's excluded, since we can't prove it isn't future news
    (look-ahead safety, #992/#1007).
    """
    market_tz = market_timezone(ticker)
    if pub_date is not None:
        if isinstance(pub_date, date) and not isinstance(pub_date, datetime):
            local_pub_date = pub_date
        elif not isinstance(pub_date, datetime):
            return False
        elif pub_date.tzinfo is None:
            local_pub_date = pub_date.date()
        else:
            local_pub_date = pub_date.astimezone(market_tz).date()
        return start_dt.date() <= local_pub_date <= end_dt.date()
    return end_dt.date() >= (
        datetime.now(market_tz) - relativedelta(days=1)
    ).date()


def get_news_yfinance(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    Retrieve news for a specific stock ticker using yfinance.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Formatted string containing news articles
    """
    article_limit = get_config()["news_article_limit"]
    candidate_limit = max(1, min(int(get_config().get("yahoo_news_candidate_limit", 200)), 200))
    # Query Yahoo with the canonical symbol, like every other yfinance path —
    # a raw broker/forex alias (for example, XAUUSD) otherwise silently
    # returns no news. Keep the user's ticker in the report header.
    canonical = normalize_symbol(ticker)
    resolved = "" if canonical == ticker else f" (resolved to {canonical})"
    try:
        identity = resolve_search_identity(canonical)
        aliases = build_company_aliases(
            ticker,
            *identity_names(identity),
            ticker_aliases=(canonical,),
        )
        stock = yf.Ticker(canonical)
        news = yf_retry(lambda: stock.get_news(count=candidate_limit))

        if not news:
            return f"No news found for {ticker}{resolved}"

        # Parse date range for filtering
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        candidates = []
        for article in news:
            data = _extract_article_data(article)
            if _in_news_window(
                data["pub_date"], start_dt, end_dt, ticker=canonical
            ):
                candidates.append(data)

        if not candidates:
            return f"No news found for {ticker}{resolved} between {start_date} and {end_date}"

        relevant = []
        seen_titles: set[str] = set()
        for data in candidates:
            classification = classify_yahoo_article(
                data["title"], data["summary"], aliases
            )
            title_key = canonical_headline(data["title"])
            if classification.tier == "drop" or not title_key or title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            relevant.append((data, classification.tier))

        if not relevant:
            return (
                f"No relevant news found for {ticker}{resolved} between {start_date} "
                f"and {end_date} after quality filtering "
                f"({len(candidates)} in-window candidates dropped)"
            )

        from .news_selection import select_temporal

        kept = select_temporal(relevant, article_limit, start_date, end_date,
                               published=lambda row: row[0]["pub_date"])
        direct_count = sum(tier == "direct" for _, tier in kept)
        candidate_count = sum(tier == "candidate" for _, tier in kept)
        context_count = sum(tier == "context" for _, tier in kept)
        dropped_count = len(candidates) - len(relevant)
        omitted_count = len(relevant) - len(kept)
        news_str = ""
        for data, tier in kept:
            news_str += f"### [{tier}] {data['title']} (source: {data['publisher']})\n"
            if isinstance(data["pub_date"], datetime) and data["pub_date"].tzinfo is not None:
                published = data["pub_date"].astimezone(UTC).isoformat().replace("+00:00", "Z")
                news_str += f"Published: {published}\n"
            elif data["pub_date"] is not None:
                published = data["pub_date"].isoformat()
                news_str += f"Published: {published}\n"
            if data["summary"]:
                news_str += f"{data['summary']}\n"
            if data["link"]:
                news_str += f"Link: {data['link']}\n"
            news_str += "\n"

        stats = (
            f"Quality filter: upstream_returned={len(news)}; date_filtered={len(news) - len(candidates)}; "
            f"candidates={len(candidates)}; relevant={len(relevant)}; "
            f"kept={len(kept)} (direct={direct_count}, candidate={candidate_count}, "
            f"context={context_count}); "
            f"dropped={dropped_count}; omitted_by_limit={omitted_count}."
        )
        return (
            f"## {ticker}{resolved} News, from {start_date} to {end_date}:\n\n"
            f"{stats}\n\n{news_str}"
        )

    except Exception as e:
        if _is_rate_limit(e):
            raise VendorRateLimitError("Yahoo Finance rate limited the news request.") from e
        return f"Error fetching news for {ticker}: {str(e)}"


def get_global_news_yfinance(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """
    Retrieve global/macro economic news using yfinance Search.

    Args:
        curr_date: Current date in yyyy-mm-dd format
        look_back_days: Number of days to look back. ``None`` falls back to
            ``global_news_lookback_days`` from the active config.
        limit: Maximum number of articles to return. ``None`` falls back to
            ``global_news_article_limit`` from the active config.

    Returns:
        Formatted string containing global news articles
    """
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    if limit is None:
        limit = config["global_news_article_limit"]
    search_queries = config["global_news_queries"][:min(5, max(1, int(config.get("global_news_query_limit", 5))))]

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - relativedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")
    all_news = []
    seen_titles = set()

    try:
        for query in search_queries:
            search = yf_retry(lambda q=query: yf.Search(
                query=q, news_count=config.get("global_news_candidate_limit", 10), enable_fuzzy_query=True,
            ))
            for article in search.news or []:
                data = _extract_article_data(article)
                title = data["title"]
                if not _in_news_window(data["pub_date"], start_dt, curr_dt):
                    continue
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    all_news.append(article)
            if len(all_news) >= limit:
                break

        news_str = ""
        kept = 0
        for article in all_news[:limit]:
            # Extract uniformly (flat + nested) and apply the same look-ahead-safe
            # window filter, so flat articles can't leak future news (#1007).
            data = _extract_article_data(article)
            if not _in_news_window(data["pub_date"], start_dt, curr_dt):
                continue
            news_str += f"### {data['title']} (source: {data['publisher']})\n"
            from .source_observations import publish_observation

            publish_observation(
                "yfinance", "global_news_article", data["link"] or data["title"],
                {"title": data["title"], "summary": data["summary"], "link": data["link"],
                 "publisher": data["publisher"]}, available_at=data["pub_date"],
            )
            if data["pub_date"] is not None:
                news_str += f"Published: {data['pub_date'].isoformat()}\n"
            if data["summary"]:
                news_str += f"{data['summary']}\n"
            if data["link"]:
                news_str += f"Link: {data['link']}\n"
            news_str += "\n"
            kept += 1

        # All candidates fell outside the window -> say so rather than return an
        # empty-bodied report (#993).
        if kept == 0:
            return f"No global news found between {start_date} and {curr_date}"

        return f"## Global Market News, from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching global news: {str(e)}"
