"""Cross-region macro panel: prefetched and injected into the news analyst.

Macro context (rates, inflation, activity, FX) is the background *every* analysis
needs, not something to leave to the LLM to remember to look up — and a ticker's
home market is never the whole story (a Tokyo name lives or dies on the US–Japan
rate differential and the yen as much as on the BOJ). So rather than relying on
``get_macro_indicators`` tool-calls, we prefetch a compact panel and inject it
into the news prompt. Macro is market-agnostic — regions are pulled together and
compared — so this is not routed by ticker.

The panel is organised on four investment-meaning dimensions:
  * **Liquidity / rates** — the valuation anchor (discount rate for DCF/PE).
  * **Inflation** — the policy-rate outlook.
  * **Activity** — the earnings/fundamentals backdrop.
  * **Risk / FX** — cross-border capital flow (the yen hits Japanese exporters'
    P&L directly). FX/risk are global single values, not per-country, so they sit
    in their own section rather than a US/Japan column split.

**Source, for now: FRED everywhere** (it mirrors Japanese OECD series and quotes
USD/JPY, DXY, VIX), reusing the existing :mod:`fred` client and ``FRED_API_KEY`` —
zero new dependency, verifiable immediately. Gaps where FRED has no usable free
series are filled later: Japan CPI / core inflation move to **e-Stat**, the Tankan
and daily BOJ rates to **BOJ**; the panel layout is unchanged, only the provider
swaps. China becomes a fourth column when that branch lands.

Prefetched like the sentiment sources, so it **must never raise**: any per-cell
fetch failure degrades to "n/a". Look-ahead is inherited from
:func:`fred.fetch_series` (observations capped at ``curr_date``).
"""

from __future__ import annotations

import logging

from . import fred

logger = logging.getLogger(__name__)

# Region columns for the per-country sections, in display order. China becomes
# one more column here (plus its series per row) when that branch lands.
_REGIONS = ("US", "Japan")

# Per-country comparison sections: (dimension label + meaning, rows). Each row
# maps a region to a FRED alias/series ID, or ``None`` when no free source exists
# yet (rendered "n/a" without an API call — see the panel footnote). Verified live
# against FRED (2026-06): all resolve except Japan CPI/core (FRED's OECD mirrors
# are stale since ~2021 → None here, pending e-Stat). Forward-activity gauges
# (US ISM PMI — removed from FRED; JP Tankan — BOJ only) have no free FRED series,
# so they are noted in the footnote rather than shown as all-n/a rows.
_REGIONAL_SECTIONS: tuple[tuple[str, tuple[tuple[str, dict[str, str | None]], ...]], ...] = (
    ("Liquidity / rates — valuation anchor", (
        ("Policy / overnight rate", {"US": "fed_funds_rate", "Japan": "IRSTCI01JPM156N"}),
        ("10Y govt bond yield",     {"US": "10y_treasury",   "Japan": "IRLTLT01JPM156N"}),
    )),
    ("Inflation — policy outlook", (
        ("CPI (index; ~YoY via Δ)", {"US": "cpi",      "Japan": None}),
        ("Core inflation",          {"US": "core_pce", "Japan": None}),
    )),
    ("Activity — fundamentals", (
        ("Real GDP",     {"US": "real_gdp",          "Japan": "JPNRGDPEXP"}),
        ("Unemployment", {"US": "unemployment_rate", "Japan": "LRHUTTTTJPM156S"}),
    )),
)

# Cross-border risk & FX — global single values (not per-country): (label, series).
_GLOBAL_RISK: tuple[tuple[str, str], ...] = (
    ("USD/JPY",              "DEXJPUS"),
    ("Dollar index (broad)", "DTWEXBGS"),
    ("VIX",                  "VIXCLS"),
)


def _cell(indicator: str | None, curr_date: str, look_back_days: int | None) -> str:
    """Render one cell: latest value (date) + change over the window, or "n/a".

    ``None`` indicator (no free source yet) returns "n/a" without an API call.
    Never raises — any fetch/parse failure or empty series degrades to "n/a" so a
    single bad cell can't abort the prefetch (the never-raise panel contract).
    """
    if not indicator:
        return "n/a"
    try:
        data = fred.fetch_series(indicator, curr_date, look_back_days)
    except Exception as exc:
        logger.warning("Macro panel cell %s failed: %s", indicator, exc)
        return "n/a"
    if not data or not data["points"]:
        return "n/a"

    last_date, last_val = data["points"][-1]
    first_val = data["points"][0][1]
    try:
        delta = float(last_val) - float(first_val)
        return f"{last_val} ({last_date}, {delta:+.2f}/1y)"
    except (TypeError, ValueError):
        return f"{last_val} ({last_date})"


def get_global_macro_panel(curr_date: str, look_back_days: int | None = None) -> str:
    """Return a compact cross-region macro panel as of ``curr_date`` (markdown).

    A per-country comparison table (liquidity / inflation / activity, US vs Japan)
    plus a cross-border risk & FX table (USD/JPY, DXY, VIX). Each cell is the
    latest reading and its ~1-year change. Look-ahead-safe (nothing after
    ``curr_date``) and never-raising (failed/absent cells show "n/a"), so it is
    safe to prefetch and inject unconditionally into the news prompt.
    """
    rows = [
        "| Indicator | " + " | ".join(_REGIONS) + " |",
        "| --- |" + " --- |" * len(_REGIONS),
    ]
    for dimension, section in _REGIONAL_SECTIONS:
        rows.append(f"| **{dimension}** |" + " |" * len(_REGIONS))
        for label, series in section:
            cells = [_cell(series[region], curr_date, look_back_days) for region in _REGIONS]
            rows.append(f"| {label} | " + " | ".join(cells) + " |")
    regional = "\n".join(rows)

    risk_rows = ["| Risk / FX — cross-border capital flow | Latest |", "| --- | --- |"]
    for label, series_id in _GLOBAL_RISK:
        risk_rows.append(f"| {label} | {_cell(series_id, curr_date, look_back_days)} |")
    risk = "\n".join(risk_rows)

    return (
        f"## Global macro panel (as of {curr_date})\n"
        "Cross-border backdrop every analysis needs; cells show value (date) and the "
        "change over ~1 year. Read the regions together — e.g. the US–Japan rate gap "
        "drives USD/JPY, which flows straight into Japanese exporters' earnings.\n\n"
        f"{regional}\n\n{risk}\n\n"
        "_Gaps pending official sources: Japan CPI / core inflation (e-Stat); forward "
        "activity — US ISM PMI (no free series) and Japan Tankan (BOJ). China joins as "
        "a column with its own branch._"
    )
