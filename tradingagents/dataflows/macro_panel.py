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

**Source is per-cell**, so an indicator can be served by whichever provider has
the timely free series: most cells use **FRED** (US series plus the Japanese OECD
10Y mirror, and USD/JPY, DXY, VIX); **Japan CPI / core inflation** come from
**e-Stat** (FRED's OECD mirror was discontinued ~2021); and **Japan's policy rate
(daily) and Tankan DI (quarterly)** come from the **BOJ** API. Making the source
explicit per cell keeps the panel and any router-served microscope tool from
diverging on the same indicator. China becomes a fourth column when that branch
lands.

Prefetched like the sentiment sources, so it **must never raise**: any per-cell
fetch failure degrades to "n/a". Look-ahead is inherited from
:func:`fred.fetch_series` (observations capped at ``curr_date``).
"""

from __future__ import annotations

import logging

from . import boj, estat, fred

logger = logging.getLogger(__name__)

# Region columns for the per-country sections, in display order. China becomes
# one more column here (plus its series per cell) when that branch lands.
_REGIONS = ("US", "Japan")

# A cell's source: the vendor module whose ``fetch_series`` serves it. Both return
# the same shape, so _cell renders either uniformly; the explicit source keeps the
# panel from diverging from a router-served microscope tool on the same indicator.
# Stored as modules (not bound functions) so the lookup is resolved at call time —
# respecting test monkeypatching of ``<module>.fetch_series``.
_SOURCES = {
    "fred": fred,
    "estat": estat,
    "boj": boj,
}

# Per-country comparison sections. Structure:
#   (dimension label + meaning, ((row label, {region: (source, indicator)_or_None}), ...))
# Each region maps to a (source, indicator) pair, or None when no free source
# exists yet (rendered "n/a" without an API call — see the footnote). Sources by
# cell: Japan policy rate / Tankan from BOJ (daily / quarterly official), Japan
# CPI / core inflation from e-Stat (FRED's OECD mirror is discontinued ~2021),
# everything else from FRED. The one remaining gap (US ISM PMI — removed from
# FRED, no free series) stays None rather than dropping the comparison row.
_REGIONAL_SECTIONS = (
    ("Liquidity / rates — valuation anchor", (
        ("Policy / overnight rate", {"US": ("fred", "fed_funds_rate"),
                                      "Japan": ("boj", "jp_policy_rate")}),
        ("10Y govt bond yield",     {"US": ("fred", "10y_treasury"),
                                      "Japan": ("fred", "IRLTLT01JPM156N")}),
    )),
    ("Inflation — policy outlook", (
        ("CPI (index; ~YoY via Δ)", {"US": ("fred", "cpi"),
                                     "Japan": ("estat", "jp_cpi")}),
        ("Core inflation",          {"US": ("fred", "core_pce"),
                                     "Japan": ("estat", "jp_core_cpi")}),
    )),
    ("Activity — fundamentals", (
        ("Real GDP",     {"US": ("fred", "real_gdp"),
                          "Japan": ("fred", "JPNRGDPEXP")}),
        ("Unemployment", {"US": ("fred", "unemployment_rate"),
                          "Japan": ("fred", "LRHUTTTTJPM156S")}),
        ("Business sentiment (ISM PMI / Tankan DI)",
                         {"US": None,
                          "Japan": ("boj", "jp_tankan")}),
    )),
)

# Cross-border risk & FX — global single values (not per-country): (label, (source, indicator)).
_GLOBAL_RISK: tuple[tuple[str, tuple[str, str]], ...] = (
    ("USD/JPY",              ("fred", "DEXJPUS")),
    ("Dollar index (broad)", ("fred", "DTWEXBGS")),
    ("VIX",                  ("fred", "VIXCLS")),
)


def _cell(spec: tuple[str, str] | None, curr_date: str) -> str:
    """Render one cell: latest value (date) + change over the ~1y window, or "n/a".

    ``spec`` is a ``(source, indicator)`` pair, or ``None`` (no free source yet)
    which returns "n/a" without an API call. Shows the absolute change and, for
    interpretability of index series (CPI), the percent change; a single-point
    window shows just the value (no fabricated zero change). Never raises — any
    fetch/parse failure or empty series degrades to "n/a" so a single bad cell
    can't abort the prefetch (never-raise contract).
    """
    if not spec:
        return "n/a"
    source, indicator = spec
    try:
        data = _SOURCES[source].fetch_series(indicator, curr_date)
    except Exception as exc:
        logger.warning("Macro panel cell %s/%s failed: %s", source, indicator, exc)
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
        for label, specs_by_region in section:
            cells = [_cell(specs_by_region[region], curr_date) for region in _REGIONS]
            rows.append(f"| {label} | " + " | ".join(cells) + " |")
    regional = "\n".join(rows)

    risk_rows = ["| Risk / FX — cross-border capital flow | Latest |", "| --- | --- |"]
    for label, spec in _GLOBAL_RISK:
        risk_rows.append(f"| {label} | {_cell(spec, curr_date)} |")
    risk = "\n".join(risk_rows)

    return (
        f"## Global macro panel (as of {curr_date})\n"
        "Cross-border backdrop every analysis needs; cells show value (date) and the "
        "change over ~1 year. Read the regions together — e.g. the US–Japan rate gap "
        "drives USD/JPY, which flows straight into Japanese exporters' earnings.\n\n"
        f"{regional}\n\n{risk}\n\n"
        "_Sources: Japan policy rate / Tankan from BOJ (官), Japan CPI / core "
        "inflation from e-Stat (官), the rest from FRED. Remaining gap: US ISM PMI "
        "(no free series). China joins as a column with its own branch._"
    )
