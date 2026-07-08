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


def _liabilities(record: dict):
    """Total liabilities = total assets - net assets, when both are present."""
    ta, eq = _num(record.get("TA")), _num(record.get("Eq"))
    return ta - eq if ta is not None and eq is not None else None


def _period_label(record: dict) -> str:
    return (
        f"{record.get('CurPerType', '?')} end {record.get('CurPerEn', '?')} "
        f"(disclosed {record.get('DiscDate', '?')})"
    )


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

    return canonical, sorted(
        records,
        key=lambda r: (r.get("DiscDate") or "", r.get("DiscTime") or ""),
        reverse=True,
    )


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
        parts = [
            f"{label}={_fmt(spec(r) if callable(spec) else r.get(spec))}"
            for label, spec in field_specs
        ]
        lines.append(f"- {_period_label(r)}: " + ", ".join(parts))
    return "\n".join(lines)


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Headline fundamentals overview from the latest disclosed period."""
    canonical, records = _fetch_summary_periods(ticker, curr_date)
    r = records[0]
    return "\n".join([
        f"# Fundamentals overview for {canonical} (J-Quants summary)",
        f"Latest disclosure: {r.get('DocType', '?')} — {_period_label(r)}",
        f"Net sales: {_fmt(r.get('Sales'))}",
        f"Operating profit: {_fmt(r.get('OP'))}    Ordinary profit: {_fmt(r.get('OdP'))}",
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
