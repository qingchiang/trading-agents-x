"""Registry and defensive prefetch for market-specific sentiment signals.

US social feeds are handled by the analyst itself. Routed non-US markets use
this suffix registry for per-name official/professional signals, so adding a
second market does not grow another ``if is_<market>`` branch in the analyst.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from tradingagents.application.evidence_workset import StructuredNumericFact

from .cn.cn_sentiment import (
    get_holding_changes as get_cn_holding_changes,
    get_important_announcements as get_cn_important_announcements,
    get_margin_signal as get_cn_margin_signal,
    get_research_signal_payload as get_cn_research_signal_payload,
)
from .jp.edinet_holdings import get_large_holdings
from .jp.jquants_sentiment import get_margin_balance, get_short_positions
from .jp.yfinance_sentiment import get_analyst_ratings_payload
from .lookahead import is_near_live
from .source_observations import SourceObservation, capture_observations
from .symbol_utils import match_exchange_suffix

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SentimentSignal:
    """One market-specific signal and its provenance contract."""

    tag: str
    fetch: Callable[
        [str, str],
        str | tuple[str, tuple[StructuredNumericFact, ...]],
    ]
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
    structured_numeric_facts: tuple[StructuredNumericFact, ...] = ()
    observations: tuple[SourceObservation, ...] = ()


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
            fetch=get_analyst_ratings_payload,
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


def _cn_signals() -> tuple[SentimentSignal, ...]:
    """Build mainland specs at lookup time for patchable, never-raise prefetch."""
    from .lookahead import lookback_start_date

    return (
        SentimentSignal(
            tag="cn_margin",
            fetch=get_cn_margin_signal,
            evidence="margin financing and securities lending",
            source="SSE/SZSE",
            title="Margin positioning — official exchange detail",
            intro=(
                "Per-name financing and securities-lending balances. Treat them as "
                "positioning/overhang, not as a directional verdict; missing coverage "
                "or a failed exchange request is unknown, never neutral or bearish."
            ),
            effective=lambda date: f"latest exchange session <= {date}",
            timing="trade-date filtered",
        ),
        SentimentSignal(
            tag="cn_holding_changes",
            fetch=get_cn_holding_changes,
            evidence="major-shareholder and executive holding changes",
            source="Eastmoney disclosures / CNINFO fallback",
            title="Insider & major-shareholder holding changes",
            intro=(
                "Records use available disclosure/update dates. When a source exposes "
                "only the event date, that record is labelled non-strict point-in-time. "
                "Interpret the named holder, direction and size; no events or unavailable "
                "coverage does not imply neutral sentiment."
            ),
            effective=lambda date: f"{lookback_start_date(date, 89)} to {date}",
            timing="mixed disclosure/update-date and event-date filtering; see record labels",
        ),
        SentimentSignal(
            tag="cn_research",
            fetch=get_cn_research_signal_payload,
            evidence="sell-side ratings and target prices",
            source="Sina Finance / Eastmoney Research",
            title="Sell-side rating & target-price changes",
            intro=(
                "Professional opinions published by the analysis date. Compare rating "
                "changes and target ranges across institutions; absence of coverage is unknown."
            ),
            effective=lambda date: f"{lookback_start_date(date, 89)} to {date}",
            timing="publication-date filtered",
        ),
        SentimentSignal(
            tag="cn_announcements",
            fetch=get_cn_important_announcements,
            evidence="material company announcements",
            source="CNINFO",
            title="Important company announcements — official CNINFO",
            intro=(
                "Exact-code official disclosures matching material-event terms. Read "
                "the event itself; an empty or unavailable feed is not a sentiment score."
            ),
            effective=lambda date: f"{lookback_start_date(date, 29)} to {date}",
            timing="disclosure-date filtered",
        ),
    )


_SIGNAL_FACTORIES: dict[str, Callable[[], tuple[SentimentSignal, ...]]] = {
    ".T": _jp_signals,
    ".SS": _cn_signals,
    ".SZ": _cn_signals,
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
        structured_numeric_facts: tuple[StructuredNumericFact, ...] = ()
        observations = []
        if spec.live_only and not is_near_live(curr_date, ticker):
            body = (
                "<live-only source unavailable for historical or future "
                f"trade_date {curr_date}; vendor not queried>"
            )
        else:
            try:
                with capture_observations() as observations:
                    result = spec.fetch(ticker, curr_date)
                if isinstance(result, tuple):
                    body, structured_numeric_facts = result
                else:
                    body = result or ""
            except Exception as exc:
                logger.warning(
                    "Sentiment signal %s failed for %s: %s", spec.tag, ticker, exc
                )
                body = f"<{spec.source} unavailable: {type(exc).__name__}>"
        retrieved_at = None
        if spec.live_only and body and "unavailable" not in body.casefold():
            retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")
        fetched.append(
            FetchedSentimentSignal(
                spec,
                body,
                retrieved_at,
                tuple(structured_numeric_facts),
                tuple(sorted(
                    observations,
                    key=lambda o: str(o.available_on or o.effective_date or ""),
                    reverse=True,
                )[:8] if spec.tag == "cn_holding_changes" else observations),
            )
        )
    return tuple(fetched)
