"""StockTwits public symbol-stream fetcher.

StockTwits exposes a per-symbol message stream at
``api.stocktwits.com/api/2/streams/symbol/{ticker}.json`` that requires no
API key, no OAuth, and no registration. Each message includes a
user-labeled sentiment field (``Bullish``/``Bearish``/null), the message
body, timestamp, and posting user.

The function is deliberately self-contained: short timeout, graceful
degradation on any HTTP or parse failure, and a string return type so
the calling agent gets a uniform interface regardless of whether the
network call succeeded.
"""

from __future__ import annotations

import http.client
import json
import logging
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from tradingagents.version import IDENTIFIED_USER_AGENT

logger = logging.getLogger(__name__)

_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_UA = IDENTIFIED_USER_AGENT


def _stocktwits_symbol(ticker: str) -> str:
    """Return the supported equity symbol in StockTwits form."""
    return ticker.strip().upper()


def _new_york_datetime(created_at: str) -> datetime | None:
    """Convert a StockTwits timestamp to its supported US-feed timezone."""
    try:
        normalized = created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
        created = datetime.fromisoformat(normalized)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        market_tz = ZoneInfo("America/New_York")
        return created.astimezone(market_tz)
    except (TypeError, ValueError):
        return None


def _in_window(created_at: str, start_date: str, end_date: str) -> bool:
    """Whether a UTC StockTwits timestamp falls in the target market-date window."""
    try:
        local = _new_york_datetime(created_at)
        if local is None:
            return False
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    return start <= local.date() <= end


def fetch_stocktwits_messages(
    ticker: str,
    limit: int = 30,
    timeout: float = 10.0,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Fetch recent StockTwits messages for ``ticker`` and return them as a
    formatted plaintext block ready for prompt injection.

    Returns a placeholder string when the endpoint is unreachable, the
    symbol has no messages, or the response shape is unexpected — the
    caller never has to special-case None or exceptions.
    """
    url = _API.format(ticker=_stocktwits_symbol(ticker))
    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        # OSError covers URLError/TimeoutError/connection resets; HTTPException
        # covers chunked-transfer errors (IncompleteRead/BadStatusLine, #1024).
        logger.warning("StockTwits fetch failed for %s: %s", ticker, exc)
        return f"<stocktwits unavailable: {type(exc).__name__}>"

    messages = data.get("messages", []) if isinstance(data, dict) else []
    if not messages:
        return f"<no StockTwits messages found for ${ticker.upper()}>"

    if start_date is not None or end_date is not None:
        if not start_date or not end_date:
            return "<stocktwits unavailable: both start_date and end_date are required>"
        messages = [
            message
            for message in messages
            if _in_window(str(message.get("created_at") or ""), start_date, end_date)
        ]
        if not messages:
            return (
                f"<no StockTwits messages for ${ticker.upper()} in requested window "
                f"{start_date}..{end_date} among the current public feed sample; "
                "this is not evidence of no historical discussion>"
            )

    lines = []
    bullish = bearish = unlabeled = 0
    for m in messages[:limit]:
        created = m.get("created_at", "")
        displayed_at = created
        if start_date:
            local = _new_york_datetime(str(created))
            if local is not None:
                displayed_at = local.strftime("%Y-%m-%d %H:%M:%S %Z")
        user = (m.get("user") or {}).get("username", "?")
        entities = m.get("entities") or {}
        sentiment_obj = entities.get("sentiment") or {}
        sentiment = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None
        body = (m.get("body") or "").replace("\n", " ").strip()
        if len(body) > 280:
            body = body[:280] + "…"

        if sentiment == "Bullish":
            bullish += 1
            tag = "Bullish"
        elif sentiment == "Bearish":
            bearish += 1
            tag = "Bearish"
        else:
            unlabeled += 1
            tag = "no-label"
        lines.append(f"[{displayed_at} · @{user} · {tag}] {body}")

    total = bullish + bearish + unlabeled
    bull_pct = round(100 * bullish / total) if total else 0
    bear_pct = round(100 * bearish / total) if total else 0
    summary = (
        f"Bullish: {bullish} ({bull_pct}%) · "
        f"Bearish: {bearish} ({bear_pct}%) · "
        f"Unlabeled: {unlabeled} · "
        f"Total: {total} "
        + (f"messages in {start_date}..{end_date}" if start_date else "most-recent messages")
    )
    return summary + "\n\n" + "\n".join(lines)
