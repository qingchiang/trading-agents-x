"""Market-wide investor-flow signal for the Japanese sentiment proxy.

Japanese retail sentiment has no clean free API (X is paid; Yahoo!ファイナンス
掲示板 / みんかぶ / 株探 are scrape-only and ToS-grey), so for ``.T`` tickers we
substitute an official quantitative signal: J-Quants' weekly *Trading by Type of
Investors* (投資部門別売買状況, ``/equities/investor-types``, Light plan). It
reports net buy/sell by investor category per TSE section — foreigners are the
dominant driver of Japanese equities, individuals often trade contrarian — which
is a far more reliable "who is buying" read than scraped forum chatter.

The data is **market-section level, not per-ticker**, so it is injected into the
sentiment prompt as market-wide context (alongside the per-ticker EDINET news),
the same way global news contextualises a single-name analysis. A per-ticker
large-shareholding (大量保有) signal from EDINET is a planned later addition.

This is pre-fetched by the sentiment analyst (not routed through
``route_to_vendor``), so like the StockTwits/Reddit fetchers it must always
return a string and never raise.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .jquants_common import fetch_records

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


def _net(value) -> str:
    """Format a net-balance value with sign and thousands separators, or N/A."""
    try:
        return f"{float(value):+,.0f}"
    except (TypeError, ValueError):
        return "N/A"


def _format_week(record: dict) -> str:
    flows = " · ".join(f"{label} {_net(record.get(key))}" for label, key in _FLOW_FIELDS)
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
    if not str(ticker).upper().endswith(".T"):
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
