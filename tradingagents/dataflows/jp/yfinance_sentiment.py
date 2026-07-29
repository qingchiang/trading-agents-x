"""Analyst-consensus rating overlay for the Japanese sentiment proxy.

yfinance covers Japanese large/mid caps with sell-side analyst ratings and a
12-month price-target band — a per-name *opinion* signal. Exchange-section
J-Quants investor flows (投資部門別) are market context in the News Analyst, not a
substitute for this signal. ``.info`` is a LIVE snapshot with no as-of history, so this
overlay is gated to live / near-live runs (see :mod:`.lookahead`): a backtest
simply omits it rather than leaking today's ratings onto a past date.

Pre-fetched by the sentiment analyst (not routed through ``route_to_vendor``),
so like the other prefetch sources it must always return a string and never
raise.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..lookahead import is_near_live
from ..y_finance import get_analyst_ratings
from .jquants_common import parse_number as _num
from .market import is_tokyo_ticker

logger = logging.getLogger(__name__)

# yfinance's recommendationMean is a 1–5 scale; spell it out so the LLM reads a
# low number as bullish, not the reverse.
_MEAN_SCALE = "1=Strong Buy … 3=Hold … 5=Strong Sell"


def get_analyst_ratings_block(ticker: str, curr_date: str) -> str:
    """Return a live-only analyst-consensus rating block for a ``.T`` ticker, else "".

    Empty for non-Japanese tickers (yfinance-sourced but injected as a JP fill;
    another market supplies its own) and empty outside the market-local near-live
    gate (``.info`` is a live snapshot that would leak the future). Degrades to "" on
    any fetch error or when no rating/target is available — never raises (the
    sentiment prefetch contract).
    """
    if not is_tokyo_ticker(ticker):
        return ""
    if not is_near_live(curr_date, ticker):
        return ""
    try:
        ratings = get_analyst_ratings(ticker)
    except Exception as exc:  # defensive: the getter already degrades to {}
        logger.warning("Analyst-ratings fetch failed for %s: %s", ticker, exc)
        return ""

    key = ratings.get("recommendationKey")
    mean = _num(ratings.get("recommendationMean"))
    n_analysts = _num(ratings.get("numberOfAnalystOpinions"))
    tgt_mean = _num(ratings.get("targetMeanPrice"))

    lines = []
    # Rating line whenever there is a real rating, independent of the analyst
    # count — yfinance can report a rating with numberOfAnalystOpinions None/0, so
    # the count is an optional trailing clause, not a gate on the whole block.
    if key and key != "none":
        mean_note = f" (mean {mean:.2f} on {_MEAN_SCALE})" if mean is not None else ""
        count = f"; {int(n_analysts)} analysts" if n_analysts else ""
        lines.append(f"- Rating: {str(key).replace('_', ' ')}{mean_note}{count}")

    if tgt_mean is not None:
        hi = _num(ratings.get("targetHighPrice"))
        lo = _num(ratings.get("targetLowPrice"))
        bounds = [f"{name} {v:,.0f}" for name, v in (("high", hi), ("low", lo)) if v is not None]
        band = f" ({' / '.join(bounds)})" if bounds else ""
        # currentPrice is sometimes absent for .T; fall back to regularMarketPrice
        # so the implied-upside (the headline number) isn't silently dropped.
        current = _num(ratings.get("currentPrice")) or _num(ratings.get("regularMarketPrice"))
        upside = ""
        if current and current > 0:
            upside = (
                f" — implied {(tgt_mean / current - 1) * 100:+.1f}% vs "
                f"retrieval-time current price {current:,.0f}"
            )
        lines.append(f"- 12-month price target (mean): {tgt_mean:,.0f}{band}{upside}")

    if not lines:
        return ""
    # Data + legend only; the prompt wrapper owns the section framing and the
    # sentiment rules own how to weight it (kept out of here so they don't drift).
    retrieved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "yfinance analyst consensus (sell-side; LIVE snapshot)\n"
        f"Requested analysis date: {curr_date}\n"
        f"Retrieved at: {retrieved_at}\n"
        "Not point-in-time historical data; price comparisons use the retrieval-time price.\n\n"
        + "\n".join(lines)
    )
