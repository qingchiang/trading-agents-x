"""Registry and defensive prefetch for market-specific sentiment signals.

US social feeds are handled by the analyst itself. Routed non-US markets use
this suffix registry for per-name official/professional signals, so adding a
second market does not grow another ``if is_<market>`` branch in the analyst.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .jp.edinet_holdings import get_large_holdings
from .jp.jquants_sentiment import get_margin_balance, get_short_positions
from .jp.yfinance_sentiment import get_analyst_ratings_block
from .symbol_utils import match_exchange_suffix

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SentimentSignal:
    """One market-specific signal and its provenance contract."""

    tag: str
    fetch: Callable[[str, str], str]
    evidence: str
    source: str
    title: str
    intro: str
    effective: Callable[[str], str]
    timing: str
    live_only: bool = False


@dataclass(frozen=True)
class FetchedSentimentSignal:
    """Defensively fetched signal, including optional retrieval time."""

    spec: SentimentSignal
    body: str
    retrieved_at: str | None = None


def _jp_signals() -> tuple[SentimentSignal, ...]:
    """Build Tokyo specs at lookup time so tests and callers can patch fetchers."""
    from .lookahead import lookback_start_date

    return (
        SentimentSignal(
            tag="large_holdings",
            fetch=get_large_holdings,
            evidence="ownership and control filings",
            source="EDINET",
            title="Ownership & control — official 大量保有 / 公開買付 (TOB)",
            intro=(
                "Per-name EDINET filings about the company, of two kinds — read each "
                "row's label.\n大量保有 (5%+ stakes): an investor crossing/adjusting a "
                "5% stake; the row shows the filer and report type, not the exact %, so "
                "read frequency and who is filing — a cluster of new 5%+ reports "
                "suggests institutional accumulation (mildly bullish).\n公開買付 (TOB / "
                "tender offer): a takeover event that dominates routine accumulation — "
                "a launch is a premium bid (strongly bullish for the target), a "
                "withdrawal cancels it (bearish), a result concludes it, and a "
                "target-board opinion signals support or opposition. Weigh a takeover "
                "by its label, not as a 5% stake."
            ),
            effective=lambda date: f"{lookback_start_date(date, 89)} to {date}",
            timing="disclosure-date filtered",
        ),
        SentimentSignal(
            tag="margin_balances",
            fetch=get_margin_balance,
            evidence="margin balances",
            source="J-Quants",
            title="Margin-trading balances — official weekly 信用取引",
            intro=(
                "Per-name weekly margin-trading balances (信用取引): 信用買残 are shares "
                "bought on margin (latent future selling), 信用売残 shares sold short on "
                "margin. A rising credit ratio (買残/売残) means growing long overhang — "
                "a contrarian/bearish tilt, a falling one is supportive. Read the trend "
                "across weeks, not a single week."
            ),
            effective=lambda date: f"published weeks <= {date}",
            timing="publication-date filtered",
        ),
        SentimentSignal(
            tag="short_positions",
            fetch=get_short_positions,
            evidence="large short positions",
            source="J-Quants",
            title="Short-position disclosures — official 空売り残高報告",
            intro=(
                "Per-name disclosed large short positions (空売り残高報告, ≥0.5% of "
                "shares out), each naming the short seller. New or rising positions are "
                "professional bearish positioning; falling/covered ones are bullish. "
                "Weigh by how large and how many."
            ),
            effective=lambda date: f"{lookback_start_date(date, 365)} to {date}",
            timing="disclosure-date filtered",
        ),
        SentimentSignal(
            tag="analyst_ratings",
            fetch=get_analyst_ratings_block,
            evidence="analyst consensus",
            source="yfinance",
            title="Analyst consensus — sell-side rating & price target",
            intro=(
                "Per-name sell-side view: the analyst-consensus rating (its mean is a "
                "1–5 scale where 1 is most bullish) and the 12-month price-target implied "
                "upside. A professional-opinion signal, distinct from the flow/"
                "accumulation blocks, which are positioning. LIVE snapshot — present "
                "only on live runs, absent in backtests."
            ),
            effective=lambda _date: "retrieval-time snapshot",
            timing="live non-point-in-time",
            live_only=True,
        ),
    )


_SIGNAL_FACTORIES: dict[str, Callable[[], tuple[SentimentSignal, ...]]] = {
    ".T": _jp_signals,
}


def sentiment_signal_specs(ticker: str) -> tuple[SentimentSignal, ...]:
    """Return registered per-name sentiment specs for ``ticker``'s suffix."""
    suffix = match_exchange_suffix(ticker, _SIGNAL_FACTORIES)
    factory = _SIGNAL_FACTORIES.get(suffix)
    return factory() if factory else ()


def fetch_sentiment_signals(
    ticker: str,
    curr_date: str,
) -> tuple[FetchedSentimentSignal, ...]:
    """Fetch all registered signals without allowing an exception to escape."""
    fetched = []
    for spec in sentiment_signal_specs(ticker):
        try:
            body = spec.fetch(ticker, curr_date) or ""
        except Exception as exc:
            logger.warning(
                "Sentiment signal %s failed for %s: %s", spec.tag, ticker, exc
            )
            body = f"<{spec.source} unavailable: {type(exc).__name__}>"
        retrieved_at = None
        if spec.live_only and body and "unavailable" not in body.casefold():
            retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fetched.append(FetchedSentimentSignal(spec, body, retrieved_at))
    return tuple(fetched)
