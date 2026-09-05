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
the timely free series: most cells use **FRED** (US series plus USD/JPY, DXY and
VIX); **Japan 10Y** comes from the Ministry of Finance's daily constant-maturity
curve with FRED as fallback; **Japan CPI / core inflation** come from
**e-Stat** (FRED's OECD mirror was discontinued ~2021); **Japan's policy rate
(daily) and Tankan DI (quarterly)** come from the **BOJ** API; and the China
column uses keyless Eastmoney/NBS sources. Making the source explicit per cell
keeps the panel and any router-served microscope tool from diverging on the same
indicator.

Prefetched like the sentiment sources, so it **must never raise**: any per-cell
fetch failure degrades to "n/a". Each source filters observations to the analysis
date; MOF additionally enforces its next-business-day 09:30 JST publication lag.
"""

from __future__ import annotations

import logging

from tradingagents.provenance import ProvenanceRecord, attach_provenance

from . import boj, cn_macro, estat, fred, jp_macro
from .macro_common import exact_year_over_year, summarize_points

logger = logging.getLogger(__name__)

# Region columns for the per-country sections, in display order.
_REGIONS = ("US", "Japan", "China")

# A cell's source: the vendor module whose ``fetch_series`` serves it. Both return
# the same shape, so _cell renders either uniformly; the explicit source keeps the
# panel from diverging from a router-served microscope tool on the same indicator.
# Stored as modules (not bound functions) so the lookup is resolved at call time —
# respecting test monkeypatching of ``<module>.fetch_series``.
_SOURCES = {
    "fred": fred,
    "estat": estat,
    "boj": boj,
    "cn": cn_macro,
    "jp": jp_macro,
}

_SOURCE_LABELS = {
    "fred": "FRED",
    "estat": "e-Stat",
    "boj": "BOJ",
    "cn": "China macro",
    "jp": "Japan macro",
}

# These series can switch between materially different vendors or availability
# semantics. Keep an individual audit record in addition to aggregate coverage.
_FALLBACK_AUDIT_SERIES = {
    ("jp", "jp_10y_yield"): "Japan Ministry of Finance / FRED",
    ("cn", "cn_10y_yield"): "Eastmoney / China Foreign Exchange Trade System",
    ("cn", "cn_cpi"): "National Bureau of Statistics of China / Eastmoney",
    ("cn", "cn_gdp"): "National Bureau of Statistics of China / Eastmoney",
    ("cn", "cn_pmi"): "National Bureau of Statistics of China / Eastmoney",
    ("cn", "usd_cny"): "SAFE / Eastmoney",
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
    (
        "Liquidity / rates — valuation anchor",
        (
            (
                "Policy / reference rate",
                {
                    "US": (
                        "fred",
                        "fed_funds_rate",
                        "window",
                        None,
                        "Fed funds rate [Monthly]",
                    ),
                    "Japan": (
                        "boj",
                        "jp_policy_rate",
                        "window",
                        None,
                        "BOJ policy rate [Daily]",
                    ),
                    "China": ("cn", "cn_lpr", "window", None, "1Y LPR [Monthly]"),
                },
            ),
            (
                "10Y govt bond yield",
                {
                    "US": ("fred", "10y_treasury"),
                    "Japan": ("jp", "jp_10y_yield"),
                    "China": ("cn", "cn_10y_yield"),
                },
            ),
        ),
    ),
    (
        "Inflation — policy outlook",
        (
            (
                "CPI / inflation",
                {
                    "US": ("fred", "cpi", "exact_yoy", 550),
                    "Japan": ("estat", "jp_cpi", "exact_yoy", 550),
                    "China": ("cn", "cn_cpi", "yoy_rate", 365),
                },
            ),
            (
                "Core inflation",
                {"US": ("fred", "core_pce"), "Japan": ("estat", "jp_core_cpi"), "China": None},
            ),
        ),
    ),
    (
        "Activity — fundamentals",
        (
            (
                "GDP / growth",
                {
                    "US": ("fred", "real_gdp", "exact_yoy", 550),
                    "Japan": ("fred", "JPNRGDPEXP", "exact_yoy", 550),
                    "China": ("cn", "cn_gdp", "yoy_rate", 365),
                },
            ),
            (
                "Unemployment",
                {
                    "US": ("fred", "unemployment_rate"),
                    "Japan": ("fred", "LRHUTTTTJPM156S"),
                    "China": ("cn", "cn_unemployment"),
                },
            ),
            (
                "Business sentiment (ISM PMI / Tankan DI)",
                {"US": None, "Japan": ("boj", "jp_tankan"), "China": ("cn", "cn_pmi")},
            ),
        ),
    ),
)

# Cross-border risk & FX — global single values (not per-country): (label, (source, indicator)).
_GLOBAL_RISK: tuple[tuple[str, tuple[str, str]], ...] = (
    ("USD/JPY", ("fred", "DEXJPUS")),
    ("USD/CNY central parity", ("cn", "usd_cny")),
    ("Dollar index (broad)", ("fred", "DTWEXBGS")),
    ("VIX", ("fred", "VIXCLS")),
)


def _cell(
    spec: tuple | None,
    curr_date: str,
    source_stats: dict[str, dict[str, object]] | None = None,
    unavailable_sources: dict[str, str] | None = None,
    series_records: list[ProvenanceRecord] | None = None,
) -> str:
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
    source, indicator = spec[:2]
    display = spec[2] if len(spec) >= 3 else "window"
    look_back_days = spec[3] if len(spec) >= 4 else None
    cell_label = spec[4] if len(spec) >= 5 else None
    audit_source_chain = _FALLBACK_AUDIT_SERIES.get((source, indicator))

    def audit(data: dict | None, effective: str, timing: str) -> None:
        if series_records is None or audit_source_chain is None:
            return
        actual_source = (
            str(data.get("actual_source"))
            if data and data.get("actual_source")
            else audit_source_chain
        )
        frequency = str(data.get("frequency") or "unknown") if data else "unknown"
        status = f"frequency={frequency}; {timing}"
        fallback_reason = data.get("fallback_reason") if data else None
        if fallback_reason:
            status += f"; fallback: {fallback_reason}"
        series_records.append(
            ProvenanceRecord(
                evidence=f"global macro panel / {indicator}",
                source=actual_source,
                requested=curr_date,
                effective=effective,
                timing=status,
            )
        )
    stats = None
    if source_stats is not None:
        stats = source_stats.setdefault(
            source,
            {"attempts": 0, "successes": 0, "dates": [], "timings": []},
        )
        stats["attempts"] = int(stats["attempts"]) + 1
    if unavailable_sources and source in unavailable_sources:
        if stats is not None:
            stats["unavailable"] = unavailable_sources[source]
        audit(None, "—", "retrieval unavailable")
        return "n/a"
    try:
        data = _SOURCES[source].fetch_series(indicator, curr_date, look_back_days)
        summary = summarize_points(data["points"]) if data else None
    except Exception as exc:
        logger.warning("Macro panel cell %s/%s failed: %s", source, indicator, exc)
        audit(None, "—", "retrieval unavailable")
        return "n/a"
    if summary is None:
        audit(data, "—", "available; no observations in requested window")
        return "n/a"
    if display == "exact_yoy":
        yoy = exact_year_over_year(data["points"])
        if yoy is None:
            return "n/a"
        rendered = f"{yoy.pct:+.1f}% YoY ({yoy.last_date})"
    elif display == "yoy_rate":
        try:
            value = float(summary.last_val)
        except (TypeError, ValueError):
            return "n/a"
        rendered = f"{value:+g}% YoY ({summary.last_date})"
    elif summary.delta is None:
        rendered = f"{summary.last_val} ({summary.last_date})"
    else:
        pct = f", {summary.pct:+.1f}%" if summary.pct is not None else ""
        rendered = f"{summary.last_val} ({summary.last_date}, Δ {summary.delta:+.2f}{pct})"
    audit(data, summary.last_date, str(data.get("timing") or "observation-date filtered"))
    from datetime import datetime

    from .source_observations import publish_observation

    if data.get("retrieved_at"):
        publish_observation(
            str(data.get("actual_source") or _SOURCE_LABELS.get(source, source)),
            "macro_indicator", indicator,
            {"value": summary.last_val, "observation_date": summary.last_date,
             "display": rendered, "units": data.get("units"), "frequency": data.get("frequency")},
            effective_date=summary.last_date,
            retrieved_at=datetime.fromisoformat(data["retrieved_at"]),
            timing="current macro backdrop; observation date is not a release timestamp",
            fallback=bool(data.get("fallback_reason")),
        )
    if stats is not None:
        stats["successes"] = int(stats["successes"]) + 1
        dates = stats["dates"]
        if isinstance(dates, list):
            dates.append(summary.last_date)
        timings = stats["timings"]
        if isinstance(timings, list) and data.get("timing"):
            timings.append(str(data["timing"]))
    return f"{cell_label}: {rendered}" if cell_label else rendered


def get_global_macro_panel(curr_date: str) -> str:
    """Return a compact cross-region macro panel as of ``curr_date`` (markdown).

    A per-country comparison table (liquidity / inflation / activity across the
    US, Japan and China) plus a cross-border risk & FX table. Each cell is the
    latest reading and its ~1-year change. Look-ahead-safe (nothing after
    ``curr_date``) and never-raising (failed/absent cells show "n/a"), so it is
    safe to prefetch and inject unconditionally into the news prompt.
    """
    # Disable only FRED cells for the deterministic no-key case. Keyless China and
    # BOJ cells, plus configured e-Stat cells, must remain independently available.
    unavailable_sources: dict[str, str] = {}
    try:
        fred.get_api_key()
    except fred.FredNotConfiguredError:
        unavailable_sources["fred"] = "API key is not configured"

    source_stats: dict[str, dict[str, object]] = {}
    series_records: list[ProvenanceRecord] = []
    rows = [
        "| Indicator | " + " | ".join(_REGIONS) + " |",
        "| --- |" + " --- |" * len(_REGIONS),
    ]
    for dimension, section in _REGIONAL_SECTIONS:
        rows.append(f"| **{dimension}** |" + " |" * len(_REGIONS))
        for label, specs_by_region in section:
            cells = [
                _cell(
                    specs_by_region[region],
                    curr_date,
                    source_stats,
                    unavailable_sources,
                    series_records,
                )
                for region in _REGIONS
            ]
            rows.append(f"| {label} | " + " | ".join(cells) + " |")
    regional = "\n".join(rows)

    risk_rows = ["| Risk / FX — cross-border capital flow | Latest |", "| --- | --- |"]
    for label, spec in _GLOBAL_RISK:
        risk_rows.append(
            f"| {label} | "
            f"{_cell(spec, curr_date, source_stats, unavailable_sources, series_records)} |"
        )
    risk = "\n".join(risk_rows)

    panel = (
        f"## Global macro panel (as of {curr_date})\n"
        "Cross-border backdrop every analysis needs; CPI/GDP cells show exact YoY "
        "comparisons, while other cells show value (date) and the change over ~1 year. "
        "Read the regions together — e.g. the US–Japan rate gap "
        "drives USD/JPY, which flows straight into Japanese exporters' earnings.\n\n"
        f"{regional}\n\n{risk}\n\n"
        "_Sources: Japan policy rate / Tankan from BOJ (官), Japan 10Y from "
        "Japan Ministry of Finance with FRED fallback, Japan CPI / core "
        "inflation from e-Stat (官), recent China CPI/GDP/PMI and unemployment "
        "from NBS release pages (官), China FX from SAFE / Eastmoney market data, "
        "and China 10Y from Eastmoney with a ChinaMoney bond fallback; "
        "remaining cells come from FRED. "
        "China CPI/GDP/PMI fall back to observation-period-filtered, non-vintage "
        "Eastmoney data when no eligible recent NBS release is discoverable. "
        "Remaining gaps: US ISM PMI and China core inflation._"
    )
    records = list(series_records)
    for source, stats in source_stats.items():
        attempts = int(stats["attempts"])
        successes = int(stats["successes"])
        dates = stats["dates"] if isinstance(stats["dates"], list) else []
        timings = stats["timings"] if isinstance(stats["timings"], list) else []
        effective = max(dates) if dates else "—"
        if stats.get("unavailable"):
            timing = f"unavailable: {stats['unavailable']}; 0/{attempts} cells available"
        elif successes:
            timing_modes = "; ".join(dict.fromkeys(str(value) for value in timings))
            coverage = f"{successes}/{attempts} cells available"
            timing = (
                f"{timing_modes or 'observation-date filtered'}; {coverage}"
                if successes == attempts
                else f"partial coverage; {timing_modes or 'observation-date filtered'}; "
                f"{coverage}"
            )
        else:
            timing = f"retrieval unavailable or no observations; 0/{attempts} cells available"
        records.append(
            ProvenanceRecord(
                evidence="global macro panel",
                source=_SOURCE_LABELS.get(source, source),
                requested=curr_date,
                effective=effective,
                timing=timing,
            )
        )
    return attach_provenance(panel, *records)
