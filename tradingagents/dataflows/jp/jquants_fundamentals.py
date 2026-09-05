"""J-Quants financial summaries (/fins/summary, available on the Light plan).

One endpoint carries a summarized balance sheet, income statement, and cash-flow
statement per disclosed period, so it backs all four fundamental tools. Full
line-item statements (J-Quants /fins/details) are Premium-only; EDINET XBRL is a
possible later enhancement for that detail.
"""

from __future__ import annotations

from ..symbol_utils import NoMarketDataError
from .jquants_common import (
    from_jquants_code,
    memoized_fetch,
    parse_number as _num,
    to_jquants_code,
)

# How many recent disclosed periods to show in each statement.
_PERIOD_LIMIT = 4


def _fmt(value) -> str:
    """Render a field value, showing N/A for missing/blank."""
    return "N/A" if value in (None, "") else str(value)


def _reporting_basis(record: dict) -> str:
    """Return a readable consolidation/accounting basis from ``DocType``."""
    doc_type = str(record.get("DocType") or "")
    if "NonConsolidated" in doc_type:
        scope = "Non-consolidated"
    elif "Consolidated" in doc_type:
        scope = "Consolidated"
    else:
        scope = "Scope unspecified"

    if doc_type.endswith("_IFRS"):
        standard = "IFRS"
    elif doc_type.endswith("_JP"):
        standard = "Japanese GAAP"
    elif doc_type.endswith("_US"):
        standard = "US GAAP"
    else:
        standard = "accounting standard unspecified"
    return f"{scope}, {standard}"


def _fmt_field(record: dict, key: str) -> str:
    """Render statement fields without turning accounting semantics into zeros."""
    value = record.get(key)
    if value not in (None, ""):
        return str(value)
    if key == "OdP" and str(record.get("DocType") or "").endswith("_IFRS"):
        return "not applicable (IFRS)"
    if key in {"OP", "OdP"}:
        return "not provided in J-Quants summary"
    return "N/A"


def _liabilities(record: dict):
    """Total liabilities = total assets - net assets, when both are present."""
    ta, eq = _num(record.get("TA")), _num(record.get("Eq"))
    return ta - eq if ta is not None and eq is not None else None


def _period_label(record: dict) -> str:
    return (
        f"{record.get('CurPerType', '?')} end {record.get('CurPerEn', '?')} "
        f"(disclosed {record.get('DiscDate', '?')}; {_reporting_basis(record)})"
    )


def _dedupe_periods(records: list[dict]) -> list[dict]:
    """Keep the latest visible disclosure for each fully identified period.

    ``records`` must already be sorted newest-first. Incomplete keys are retained
    because merging them could collapse distinct statement scopes or periods.
    """
    seen: set[tuple[str, str, str]] = set()
    deduped = []
    for record in records:
        key = tuple(record.get(field) for field in ("DocType", "CurPerType", "CurPerEn"))
        if any(value in (None, "") for value in key):
            deduped.append(record)
        elif key not in seen:
            seen.add(key)
            deduped.append(record)
    return deduped


# Process-local cache of raw /fins/summary records keyed by securities code. The
# four fundamental tools each fetch the same summary for a ticker, so without
# this each analysis would hit the rate-limited API four times. curr_date
# filtering/sorting is applied per call, outside the cache.
_summary_cache: dict[str, list[dict]] = {}


def _fetch_summary(code: str) -> list[dict]:
    """Fetch all /fins/summary records for ``code``, memoized per code."""
    return memoized_fetch(_summary_cache, code, "/fins/summary", {"code": code}, "data")


def _fetch_summary_periods(symbol: str, curr_date: str | None):
    """Return ``(canonical, records)`` for ``symbol``, newest disclosure first.

    Filters out periods disclosed after ``curr_date`` to prevent look-ahead bias.
    J-Quants returns rows sorted ascending by disclosure number, so we re-sort by
    disclosure date/time descending (into a new list, never mutating the cache).
    Raises NoMarketDataError when nothing usable is available.
    """
    code = to_jquants_code(symbol)
    canonical = from_jquants_code(code)
    records = _fetch_summary(code)
    if not records:
        raise NoMarketDataError(symbol, canonical, "no financial summary disclosed")

    if curr_date:
        # Require a DiscDate <= curr_date: an undated row can't be confirmed to
        # predate curr_date, so excluding it keeps the look-ahead guard sound.
        records = [r for r in records if r.get("DiscDate") and r.get("DiscDate") <= curr_date]
        if not records:
            raise NoMarketDataError(
                symbol, canonical, f"no financial summary disclosed on/before {curr_date}"
            )

    # The API returns records in ascending disclosure-number order. Preserve
    # that sequence as the final tie-breaker: corrections can share the same
    # disclosure date/time, and a stable reverse sort alone would otherwise
    # leave the older record first for deduplication.
    indexed_records = enumerate(records)
    ordered = [
        record
        for _, record in sorted(
            indexed_records,
            key=lambda item: (
                item[1].get("DiscDate") or "",
                item[1].get("DiscTime") or "",
                item[0],
            ),
            reverse=True,
        )
    ]
    return canonical, _dedupe_periods(ordered)


def fetch_periods(ticker: str, curr_date: str | None = None):
    """Public accessor: ``(canonical, records)`` for ``ticker``, newest disclosure
    first, already look-ahead filtered (``DiscDate <= curr_date``).

    Exposed for the JP fundamentals assembler (``jp_fundamentals``), which reads
    the raw summary fields (shares, dividends, forecasts, quarterly cumulatives)
    to compute valuation ratios. Keeping the fetch/filter/sort here — and the
    ratio math in the assembler — leaves this vendor responsible only for
    official data, not derived metrics. Raises ``NoMarketDataError`` when nothing
    is disclosed on/before ``curr_date``.
    """
    return _fetch_summary_periods(ticker, curr_date)


def _select(records: list[dict], freq: str) -> list[dict]:
    """Pick the recent periods, narrowing to full-year disclosures for ``annual``."""
    rows = [r for r in records if r.get("CurPerType") == "FY"] if freq == "annual" else records
    return (rows or records)[:_PERIOD_LIMIT]


def _render_periods(canonical, records, freq, title, field_specs) -> str:
    """Render one line per period; ``field_specs`` is ``(label, key-or-callable)``."""
    rows = _select(records, freq)
    lines = [f"# {title} for {canonical} (J-Quants summary, latest {len(rows)} periods)"]
    for r in rows:
        parts = []
        values = {"currency": "JPY", "period_basis": "YTD", "reporting_basis": _reporting_basis(r)}
        for label, spec in field_specs:
            value = _fmt(spec(r)) if callable(spec) else _fmt_field(r, spec)
            parts.append(f"{label}={value}")
            values[label] = spec(r) if callable(spec) else r.get(spec)
        from ..source_observations import publish_observation

        kind = "balance" if title.startswith("Balance") else "cashflow" if title.startswith("Cash") else "income"
        if kind == "balance":
            values["period_basis"] = "instant"
        publish_observation(
            "J-Quants", f"financial_{kind}",
            f"{canonical}:{r.get('CurPerEn')}:{r.get('DocType')}", values,
            effective_date=r.get("CurPerEn"), available_on=r.get("DiscDate"),
        )
        lines.append(f"- {_period_label(r)}: " + ", ".join(parts))
    return "\n".join(lines)


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Headline fundamentals overview from the latest disclosed period."""
    canonical, records = _fetch_summary_periods(ticker, curr_date)
    r = records[0]
    return "\n".join([
        f"# Fundamentals overview for {canonical} (J-Quants summary)",
        f"Latest disclosure: {r.get('DocType', '?')} — {_period_label(r)}",
        f"Reporting basis: {_reporting_basis(r)}",
        f"Net sales: {_fmt(r.get('Sales'))}",
        f"Operating profit: {_fmt_field(r, 'OP')}    "
        f"Ordinary profit: {_fmt_field(r, 'OdP')}",
        f"Net profit: {_fmt(r.get('NP'))}",
        f"EPS: {_fmt(r.get('EPS'))}    BPS: {_fmt(r.get('BPS'))}",
        f"Total assets: {_fmt(r.get('TA'))}    Net assets: {_fmt(r.get('Eq'))}",
        f"Cash flows — operating: {_fmt(r.get('CFO'))}, investing: {_fmt(r.get('CFI'))}, "
        f"financing: {_fmt(r.get('CFF'))}",
        f"Cash & equivalents (period end): {_fmt(r.get('CashEq'))}",
    ])


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """Balance-sheet summary (total assets, derived liabilities, net assets)."""
    canonical, records = _fetch_summary_periods(ticker, curr_date)
    return _render_periods(
        canonical, records, freq, "Balance sheet summary",
        [("TotalAssets", "TA"), ("TotalLiabilities", _liabilities), ("NetAssets", "Eq")],
    )


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """Cash-flow summary (operating/investing/financing + period-end cash)."""
    canonical, records = _fetch_summary_periods(ticker, curr_date)
    return _render_periods(
        canonical, records, freq, "Cash flow summary",
        [("Operating", "CFO"), ("Investing", "CFI"), ("Financing", "CFF"), ("CashEnd", "CashEq")],
    )


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """Income-statement summary (sales, operating/ordinary/net profit, EPS, BPS)."""
    canonical, records = _fetch_summary_periods(ticker, curr_date)
    return _render_periods(
        canonical, records, freq, "Income statement summary",
        [("NetSales", "Sales"), ("OperatingProfit", "OP"), ("OrdinaryProfit", "OdP"),
         ("NetProfit", "NP"), ("EPS", "EPS"), ("BPS", "BPS")],
    )
