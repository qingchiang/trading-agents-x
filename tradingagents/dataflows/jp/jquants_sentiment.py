"""J-Quants sentiment/positioning signals for the Japanese sentiment proxy.

Japanese retail sentiment has no clean free API (X is paid; Yahoo!ファイナンス
掲示板 / みんかぶ / 株探 are scrape-only and ToS-grey), so for ``.T`` tickers we
substitute official quantitative J-Quants signals for the US-retail social
platforms that don't cover Japan. Three complementary signals live here:

  * :func:`get_investor_flows` — weekly *Trading by Type of Investors*
    (投資部門別売買状況, ``/equities/investor-types``, Light plan). Net buy/sell by
    investor category per TSE section: a **market-section-level** "who is buying"
    read (foreigners dominate; individuals lean contrarian), injected as
    market-wide context the way global news contextualises a single name.
  * :func:`get_margin_balance` — weekly margin-trading balances
    (信用取引週末残高, ``/markets/margin-interest``, Standard plan). **Per-ticker**
    信用買残 / 売残 and the credit ratio (買残/売残): retail positioning and latent
    supply/demand.
  * :func:`get_short_positions` — disclosed large short positions
    (空売り残高報告, ``/markets/short-sale-report``, Standard plan). **Per-ticker**
    ≥0.5%-of-shares short disclosures, each naming the short seller: professional
    bearish positioning.

All three are pre-fetched by the sentiment analyst (not routed through
``route_to_vendor``), so like the StockTwits/Reddit fetchers each must always
return a string and never raise.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from .calendar import add_business_days
from .jquants_common import fetch_records, parse_number, to_jquants_code
from .market import is_tokyo_ticker

logger = logging.getLogger(__name__)

# Flagship TSE section: where the large, liquid, foreign-traded names sit, so its
# foreign-flow figure is the headline "foreigners buying/selling Japan" number.
_DEFAULT_SECTION = "TSEPrime"

# Net-balance fields worth surfacing, in reading order, with display labels.
# Bal = purchases − sales for that investor category (positive = net buying).
_FLOW_FIELDS = (
    ("Foreigners", "FrgnBal"),
    ("Individuals", "IndBal"),
    ("Investment trusts", "InvTrBal"),
    ("Trust banks", "TrstBnkBal"),
    ("Business cos", "BusCoBal"),
)


def _fmt_num(value, *, signed: bool = False) -> str:
    """Format a J-Quants numeric (raw or pre-parsed) with thousands separators, or N/A.

    ``signed`` prefixes ``+`` on positives, for net-balance figures where direction
    is the point.
    """
    n = parse_number(value)
    if n is None:
        return "N/A"
    return f"{n:+,.0f}" if signed else f"{n:,.0f}"


def _format_week(record: dict) -> str:
    flows = " · ".join(
        f"{label} {_fmt_num(record.get(key), signed=True)}" for label, key in _FLOW_FIELDS
    )
    span = f"{record.get('StDate', '?')}..{record.get('EnDate', '?')}"
    return f"- Week {span} (published {record.get('PubDate', '?')}): {flows}"


def get_investor_flows(
    ticker: str, curr_date: str, look_back_weeks: int = 4, section: str = _DEFAULT_SECTION
) -> str:
    """Return recent weekly investor-type net flows for the ticker's market.

    Investor-type flows are a Tokyo-market (J-Quants) signal, so this returns ""
    for any non-``.T`` ticker — a future market supplies its own source rather
    than inheriting Japan's numbers. Degrades to a placeholder string on any
    fetch error (the sentiment prefetch contract); never raises.
    """
    if not is_tokyo_ticker(ticker):
        return ""

    try:
        # Reach back a few extra weeks beyond the requested window to absorb the
        # publication lag (the latest week is released several business days
        # late). The strptime stays inside the try so a malformed curr_date
        # degrades to a placeholder rather than escaping (never-raise contract).
        start = (
            datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(weeks=look_back_weeks + 3)
        ).strftime("%Y-%m-%d")
        records = fetch_records(
            "/equities/investor-types",
            {"section": section, "from": start, "to": curr_date},
            "data",
        )
    except Exception as exc:
        logger.warning("Investor-flow fetch failed for %s: %s", section, exc)
        return f"<investor flows unavailable: {type(exc).__name__}>"

    # Look-ahead guard: a week is only known once published.
    published = [r for r in records if r.get("PubDate") and r.get("PubDate") <= curr_date]
    if not published:
        return f"<no investor-flow data published on or before {curr_date}>"

    published.sort(key=lambda r: r.get("PubDate") or "", reverse=True)
    weeks = "\n".join(_format_week(r) for r in published[:look_back_weeks])
    # Data only — a neutral source label and unit definition. The prompt wrapper
    # owns the section header/framing and the sentiment rules own how to weight
    # the categories, so the interpretation isn't restated here (would drift).
    return (
        f"{section}, weekly net flows — J-Quants 投資部門別売買状況 "
        "(net = purchases − sales; positive = net buying):\n\n"
        f"{weeks}"
    )


# --- Per-ticker margin-trading balances (信用取引週末残高) --------------------

_MARGIN_LOOK_BACK_WEEKS = 4
# 信用取引週末残高 is a snapshot as of Friday's close, published by TSE on the 2nd
# business day after (typically the following Tuesday). The endpoint carries no
# publication-date field — unlike investor-types with its PubDate — so we place the
# publication date ourselves by counting TSE trading days from the record Date. A
# fixed calendar-day lag would leak around the year-end / Golden Week closures,
# where T+2 business days can span 10+ calendar days.
_MARGIN_PUBLICATION_BUSINESS_DAYS = 2


def _margin_published_by(record_date: str, curr: date) -> bool:
    """True if a Friday-record margin week was public on/before ``curr`` (a date).

    Places the release at ``record + T+2 TSE business days``; a malformed record
    date is treated as not-yet-public (excluded) rather than raising.
    """
    try:
        rec = datetime.strptime(record_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    return add_business_days(rec, _MARGIN_PUBLICATION_BUSINESS_DAYS) <= curr


def _margin_week(record: dict) -> str:
    long_bal = parse_number(record.get("LongVol"))  # 信用買残 (margin longs)
    short_bal = parse_number(record.get("ShrtVol"))  # 信用売残 (margin shorts)
    ratio = (
        f"{long_bal / short_bal:.2f}x"
        if long_bal is not None and short_bal not in (None, 0)
        else "N/A"
    )
    return (
        f"- Week {record.get('Date', '?')}: 買残(long) {_fmt_num(long_bal)} · "
        f"売残(short) {_fmt_num(short_bal)} · credit ratio {ratio}"
    )


def get_margin_balance(
    ticker: str, curr_date: str, look_back_weeks: int = _MARGIN_LOOK_BACK_WEEKS
) -> str:
    """Return recent weekly margin balances for a ``.T`` ticker, else "".

    Empty for any non-Tokyo ticker (a future market supplies its own source) and
    empty when this name genuinely has no published margin week. On a fetch error
    or malformed ``curr_date`` it returns a visible ``<... unavailable>`` placeholder
    (never raises: the sentiment prefetch contract) so the LLM can tell a lost
    official source from a name that has none, matching the sibling flow/holdings
    signals. Look-ahead safe: a week is surfaced only once its record date's T+2
    business-day release falls on/before ``curr_date``.
    """
    if not is_tokyo_ticker(ticker):
        return ""

    try:
        end = datetime.strptime(curr_date, "%Y-%m-%d").date()
        # Reach back a few extra weeks so the publication guard (which hides at most
        # the latest week or two) still leaves a full look_back_weeks window — same
        # buffer get_investor_flows uses.
        start = (end - timedelta(weeks=look_back_weeks + 3)).strftime("%Y-%m-%d")
        records = fetch_records(
            "/markets/margin-interest",
            {"code": to_jquants_code(ticker), "from": start, "to": end.isoformat()},
            "data",
        )
    except Exception as exc:
        logger.warning("Margin-balance fetch failed for %s: %s", ticker, exc)
        return f"<margin balances unavailable: {type(exc).__name__}>"

    visible = [r for r in records if _margin_published_by(r.get("Date"), end)]
    if not visible:
        return ""
    visible.sort(key=lambda r: r.get("Date") or "", reverse=True)
    weeks = "\n".join(_margin_week(r) for r in visible[:look_back_weeks])
    # Data + legend only; the prompt wrapper owns the framing and the sentiment
    # rules own how to weight it (kept out of here so they don't drift).
    return (
        "J-Quants 信用取引 weekly margin balances (信用買残 = shares bought on margin, "
        "信用売残 = shares sold short on margin; credit ratio = 買残/売残, higher = more "
        "long overhang):\n\n"
        f"{weeks}"
    )


# --- Per-ticker disclosed large short positions (空売り残高報告) ---------------

_SHORT_LOOK_BACK_DAYS = 365
_SHORT_MAX_ROWS = 8


def _short_event(record: dict) -> str:
    seller = record.get("SSName") or "?"
    ratio = parse_number(record.get("ShrtPosToSO"))
    prev = parse_number(record.get("PrevRptRatio"))
    current = f"{ratio * 100:.2f}%" if ratio is not None else "N/A"
    trend = ""
    if ratio is not None and prev is not None:
        arrow = "↑" if ratio > prev else ("↓" if ratio < prev else "→")
        trend = f" (was {prev * 100:.2f}% {arrow})"
    return f"- {record.get('DiscDate', '?')}: {seller} — {current} of shares out{trend}"


def get_short_positions(
    ticker: str,
    curr_date: str,
    look_back_days: int = _SHORT_LOOK_BACK_DAYS,
    max_rows: int = _SHORT_MAX_ROWS,
) -> str:
    """Return recent disclosed large short positions for a ``.T`` ticker, else "".

    Empty for any non-Tokyo ticker and empty when this name has no disclosure in
    the window (common — most names are never shorted ≥0.5%). On a fetch error or
    malformed ``curr_date`` it returns a visible ``<... unavailable>`` placeholder
    (never raises: the sentiment prefetch contract). Look-ahead safe: filters on
    ``DiscDate``, the public disclosure date, against a normalized ``curr_date``, so
    a position is shown only once it was public.
    """
    if not is_tokyo_ticker(ticker):
        return ""

    try:
        # Normalize the window bounds so a parseable-but-unpadded curr_date (e.g.
        # "2026-7-5") can't mis-order a lexical date compare and leak the future.
        end = datetime.strptime(curr_date, "%Y-%m-%d").date()
        start = (end - timedelta(days=look_back_days)).isoformat()
        end_iso = end.isoformat()
        # Bound the fetch at curr_date to cap the payload (safe regardless of which
        # date field the server filters: CalcDate <= DiscDate, so no in-window
        # disclosure is dropped). The look_back_days lower bound stays client-side —
        # the client filter on DiscDate is what enforces look-ahead safety.
        records = fetch_records(
            "/markets/short-sale-report",
            {"code": to_jquants_code(ticker), "to": end_iso},
            "data",
        )
    except Exception as exc:
        logger.warning("Short-position fetch failed for %s: %s", ticker, exc)
        return f"<short positions unavailable: {type(exc).__name__}>"

    # A disclosure with no parseable position ratio carries no magnitude, so drop
    # it rather than render a bare "SELLER — N/A of shares out" bearish-looking row.
    visible = [
        r
        for r in records
        if r.get("DiscDate")
        and start <= r["DiscDate"] <= end_iso
        and parse_number(r.get("ShrtPosToSO")) is not None
    ]
    if not visible:
        return ""
    visible.sort(key=lambda r: r.get("DiscDate") or "", reverse=True)
    events = "\n".join(_short_event(r) for r in visible[:max_rows])
    return (
        "J-Quants 空売り残高報告 — disclosed large short positions (≥0.5% of shares "
        "outstanding), each naming the short seller:\n\n"
        f"{events}"
    )
