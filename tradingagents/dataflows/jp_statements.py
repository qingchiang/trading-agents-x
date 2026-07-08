"""JP statement assembler: J-Quants official summary + curated yfinance line items.

J-Quants' ``/fins/summary`` carries only a handful of aggregated lines per period
(sales, operating/ordinary/net profit, EPS/BPS; total assets/liabilities/equity;
the three cash-flow subtotals) — but it is the freshest official filing. yfinance
carries granular line items but is analyst-compiled and can lag. So for ``.T``
statements we lead with the J-Quants summary (authoritative, latest) and append a
curated set of yfinance line items the summary lacks (gross profit, SG&A, EBITDA,
interest, capex, free cash flow, debt, working capital, …).

Both sources are date-safe — J-Quants filters by ``DiscDate <= curr_date`` and the
yfinance frame is filtered to fiscal columns ``<= curr_date`` (unlike ``.info``,
statements have real as-of history) — so appending yfinance introduces no
look-ahead. The J-Quants base is authoritative and non-optional: if it has no
data the error propagates so the router can fall through; the yfinance detail is
best-effort and simply omitted on any failure.
"""

from __future__ import annotations

import logging

from . import jquants_fundamentals as jqf
from .y_finance import get_statement_frame

logger = logging.getLogger(__name__)

# Curated yfinance line items that COMPLEMENT the J-Quants summary — only rows it
# doesn't already carry, keyed by statement kind. Matched tolerantly by label
# (missing rows are skipped), rendered in this order.
_CURATED = {
    "income": [
        "Cost Of Revenue", "Gross Profit", "Selling General And Administration",
        "EBITDA", "EBIT", "Interest Expense", "Pretax Income",
    ],
    "balance": [
        "Cash And Cash Equivalents", "Accounts Receivable", "Inventory",
        "Current Assets", "Net PPE", "Current Liabilities", "Total Debt",
        "Net Debt", "Working Capital", "Retained Earnings",
    ],
    "cashflow": [
        "Capital Expenditure", "Free Cash Flow", "Depreciation And Amortization",
        "Change In Working Capital", "Cash Dividends Paid", "Repurchase Of Capital Stock",
    ],
}


def _detail_block(ticker: str, kind: str, freq: str, curr_date: str | None) -> str:
    """Curated yfinance line-item block to append, or '' when unavailable.

    Best-effort: any failure or absence of the curated rows returns '' so the
    official J-Quants summary still renders on its own.
    """
    # Look-ahead guard: without a curr_date, filter_financials_by_date can't drop
    # future fiscal columns, so skip the yfinance append rather than risk a leak.
    if not curr_date:
        return ""
    try:
        frame = get_statement_frame(ticker, kind, freq, curr_date)
    except Exception as exc:  # never let the detail append break the summary
        logger.warning("JP statements: yfinance detail failed for %s (%s): %s", ticker, kind, exc)
        return ""
    if frame is None:
        return ""
    rows = [r for r in _CURATED[kind] if r in frame.index]
    if not rows:
        # Frame present but no curated label matched — likely a yfinance label
        # rename; log so the silent degradation is detectable.
        logger.debug("JP statements: no curated %s rows matched for %s (label drift?)", kind, ticker)
        return ""
    # Drop periods/rows yfinance hasn't filled (its line items lag the J-Quants
    # summary ~1 FY, leaving an all-blank latest column or an empty curated row).
    sub = frame.loc[rows].dropna(axis=1, how="all").dropna(axis=0, how="all")
    if sub.empty:
        return ""
    return (
        f"\n\n## Line-item detail (yfinance, curated, date-safe as of {curr_date}, may lag)\n"
        + sub.to_csv()
    )


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """J-Quants income summary + curated yfinance line items."""
    base = jqf.get_income_statement(ticker, freq, curr_date)
    return base + _detail_block(ticker, "income", freq, curr_date)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """J-Quants balance-sheet summary + curated yfinance line items."""
    base = jqf.get_balance_sheet(ticker, freq, curr_date)
    return base + _detail_block(ticker, "balance", freq, curr_date)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """J-Quants cash-flow summary + curated yfinance line items."""
    base = jqf.get_cashflow(ticker, freq, curr_date)
    return base + _detail_block(ticker, "cashflow", freq, curr_date)
