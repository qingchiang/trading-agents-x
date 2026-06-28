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

# Per-country comparison sections. Structure:
#   (dimension label + meaning, ((row label, {region: series_id_or_None}), ...))
# A region maps to a FRED alias/series ID, or None when no free source exists yet
# (rendered "n/a" without an API call — see the footnote). Japan CPI/core are None:
# FRED's OECD mirrors are discontinued (~2021), pending e-Stat. Forward-activity
# gauges (US ISM PMI — removed from FRED; JP Tankan — BOJ only) have no free FRED
# series, so they live in the footnote rather than as all-n/a rows.
_REGIONAL_SECTIONS = (
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


def _cell(indicator: str | None, curr_date: str) -> str:
    """Render one cell: latest value (date) + change over the ~1y window, or "n/a".

    ``None`` indicator (no free source yet) returns "n/a" without an API call.
    Shows the absolute change and, for interpretability of index series (CPI), the
    percent change; a single-point window shows just the value (no fabricated zero
    change). Never raises — any fetch/parse failure or empty series degrades to
    "n/a" so a single bad cell can't abort the prefetch (never-raise contract).
    """
    if not indicator:
        return "n/a"
    try:
        data = fred.fetch_series(indicator, curr_date)
    except Exception as exc:
        logger.warning("Macro panel cell %s failed: %s", indicator, exc)
        return "n/a"
    summary = fred.summarize_points(data["points"]) if data else None
    if summary is None:
        return "n/a"
    if summary.delta is None:
        return f"{summary.last_val} ({summary.last_date})"
    pct = f", {summary.pct:+.1f}%" if summary.pct is not None else ""
    return f"{summary.last_val} ({summary.last_date}, Δ {summary.delta:+.2f}{pct})"


def get_global_macro_panel(curr_date: str) -> str:
    """Return a compact cross-region macro panel as of ``curr_date`` (markdown).

    A per-country comparison table (liquidity / inflation / activity, US vs Japan)
    plus a cross-border risk & FX table (USD/JPY, DXY, VIX). Each cell is the
    latest reading and its ~1-year change. Look-ahead-safe (nothing after
    ``curr_date``) and never-raising (failed/absent cells show "n/a"), so it is
    safe to prefetch and inject unconditionally into the news prompt.
    """
    # One global check for the deterministic "no key" case, so an unconfigured FRED
    # short-circuits to a clear note instead of 13 raise+log cycles all showing n/a.
    try:
        fred.get_api_key()
    except fred.FredNotConfiguredError:
        return (
            f"## Global macro panel (as of {curr_date})\n"
            "_Macro panel unavailable: FRED_API_KEY is not configured._"
        )

    rows = [
        "| Indicator | " + " | ".join(_REGIONS) + " |",
        "| --- |" + " --- |" * len(_REGIONS),
    ]
    for dimension, section in _REGIONAL_SECTIONS:
        rows.append(f"| **{dimension}** |" + " |" * len(_REGIONS))
        for label, series in section:
            cells = [_cell(series[region], curr_date) for region in _REGIONS]
            rows.append(f"| {label} | " + " | ".join(cells) + " |")
    regional = "\n".join(rows)

    risk_rows = ["| Risk / FX — cross-border capital flow | Latest |", "| --- | --- |"]
    for label, series_id in _GLOBAL_RISK:
        risk_rows.append(f"| {label} | {_cell(series_id, curr_date)} |")
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
