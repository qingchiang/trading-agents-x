"""JP statement assembler: J-Quants official summary + curated yfinance line items.

J-Quants' ``/fins/summary`` carries only a handful of aggregated lines per period
(sales, operating/ordinary/net profit, EPS/BPS; total assets/liabilities/equity;
the three cash-flow subtotals) — but it is the freshest official filing. yfinance
carries granular line items but is analyst-compiled and can lag. So for ``.T``
statements we lead with the J-Quants summary (authoritative, latest) and append a
curated set of yfinance line items the summary lacks (gross profit, SG&A, EBITDA,
interest, capex, free cash flow, debt, working capital, …).

J-Quants is point-in-time safe because it filters by ``DiscDate <= curr_date``.
yfinance exposes current statement frames with fiscal-period-end columns but no
historical publication timestamp, so its detail is live-only: historical runs
receive the official summary plus an explicit unavailable note. The J-Quants
base is authoritative and non-optional; live yfinance detail is best-effort.
For direct callers that omit ``curr_date``, the legacy live-retrieval behavior
is retained and labelled explicitly; graph tools inject the analysis date from
workflow state rather than accepting an LLM-supplied value.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from tradingagents.provenance import (
    ProvenanceRecord,
    attach_evidence_span,
    attach_provenance,
)

from ..lookahead import is_near_live
from ..y_finance import get_statement_frame
from . import jquants_fundamentals as jqf

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

_EVIDENCE_BY_KIND = {
    "income": "get_income_statement",
    "balance": "get_balance_sheet",
    "cashflow": "get_cashflow",
}


def _historical_detail_note(curr_date: str | None) -> str:
    requested = curr_date or "not provided"
    return (
        "\n\n## Line-item detail unavailable for historical analysis\n"
        f"Requested analysis date: {requested}. Current yfinance statement frames "
        "do not expose point-in-time filing timestamps, so they were not requested. "
        "Use the J-Quants disclosure-date-filtered summary above; do not estimate "
        "missing line items."
    )


def _no_date_live_note(curr_date: str | None) -> str:
    """Explain the compatibility live mode used only when no date is supplied."""
    if curr_date is not None:
        return ""
    return (
        "\n\n## Retrieval mode\n"
        "No analysis date was provided; this call is treated as a live retrieval. "
        "The J-Quants summary above is the latest disclosure available at retrieval "
        "time and was not filtered to a historical cutoff."
    )


def _requested_date_label(curr_date: str | None) -> str:
    return curr_date or "not provided (treated as live retrieval)"


def _detail_status_marker(
    kind: str,
    curr_date: str | None,
    timing: str,
) -> str:
    """Return metadata-only yfinance status without a visible empty block."""
    return _detail_evidence_span(
        attach_provenance(
            "",
            ProvenanceRecord(
                evidence=_EVIDENCE_BY_KIND[kind],
                source="yfinance curated detail",
                requested=_requested_date_label(curr_date),
                effective="—",
                timing=f"live-only {timing}",
            ),
        ),
    )


def _detail_evidence_span(content: str) -> str:
    return attach_evidence_span(content, temporal_scope="live_only")


def _detail_block(ticker: str, kind: str, freq: str, curr_date: str | None) -> str:
    """Curated live yfinance line-item block, or a safe historical note.

    Historical/malformed dates never trigger yfinance. A missing date preserves
    the direct-call live compatibility mode and is labelled as such. For a live
    request, any failure or absence of curated rows returns '' so the official
    J-Quants summary still renders on its own.
    """
    if curr_date is not None and not is_near_live(curr_date, ticker):
        return _detail_evidence_span(
            attach_provenance(
                _historical_detail_note(curr_date),
                ProvenanceRecord(
                    evidence=_EVIDENCE_BY_KIND[kind],
                    source="yfinance curated detail",
                    requested=curr_date,
                    effective="—",
                    timing=(
                        "live-only; unavailable for historical or future "
                        "date; vendor not queried"
                    ),
                ),
            ),
        )
    try:
        frame = get_statement_frame(ticker, kind, freq, curr_date)
    except Exception as exc:  # never let the detail append break the summary
        logger.warning("JP statements: yfinance detail failed for %s (%s): %s", ticker, kind, exc)
        return _detail_status_marker(kind, curr_date, "retrieval unavailable")
    if frame is None:
        return _detail_status_marker(kind, curr_date, "retrieval unavailable")
    rows = [r for r in _CURATED[kind] if r in frame.index]
    if not rows:
        # Frame present but no curated label matched — likely a yfinance label
        # rename; log so the silent degradation is detectable.
        logger.debug("JP statements: no curated %s rows matched for %s (label drift?)", kind, ticker)
        return _detail_status_marker(
            kind, curr_date, "available; no curated line items matched"
        )
    # Drop periods/rows yfinance hasn't filled (its line items lag the J-Quants
    # summary ~1 FY, leaving an all-blank latest column or an empty curated row).
    sub = frame.loc[rows].dropna(axis=1, how="all").dropna(axis=0, how="all")
    if sub.empty:
        return _detail_status_marker(
            kind, curr_date, "available; curated line items were empty"
        )
    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
    block = (
        "\n\n## Line-item detail (yfinance, curated live snapshot, may lag)\n"
        f"Requested analysis date: {_requested_date_label(curr_date)}\n"
        f"Retrieval timestamp: {retrieved}\n"
        "Not point-in-time historical data; fiscal-period-end filtering does not "
        "establish when these values were published.\n"
        + sub.to_csv()
    )
    return _detail_evidence_span(
        attach_provenance(
            block,
            ProvenanceRecord(
                evidence=_EVIDENCE_BY_KIND[kind],
                source="yfinance curated detail",
                requested=_requested_date_label(curr_date),
                effective="current statement frame; fiscal period ends only",
                timing="live non-point-in-time; may lag",
                retrieved_at=retrieved,
            ),
        ),
    )


def _with_official_provenance(
    text: str, kind: str, curr_date: str | None
) -> str:
    requested = _requested_date_label(curr_date)
    effective = (
        f"disclosures <= {curr_date}"
        if curr_date is not None
        else "latest disclosure at retrieval"
    )
    timing = (
        "disclosure-date filtered"
        if curr_date is not None
        else "live retrieval; no historical cutoff supplied"
    )
    return attach_provenance(
        text,
        ProvenanceRecord(
            evidence=_EVIDENCE_BY_KIND[kind],
            source="J-Quants official summary",
            requested=requested,
            effective=effective,
            timing=timing,
        ),
    )


def _frontier_kwargs(information_frontier: str | None) -> dict[str, str]:
    return (
        {"information_frontier": information_frontier}
        if information_frontier is not None
        else {}
    )


def get_income_statement(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str | None = None,
    *,
    information_frontier: str | None = None,
) -> str:
    """J-Quants income summary + curated yfinance line items."""
    base = jqf.get_income_statement(
        ticker,
        freq,
        curr_date,
        **_frontier_kwargs(information_frontier),
    )
    result = base + _no_date_live_note(curr_date) + _detail_block(ticker, "income", freq, curr_date)
    return _with_official_provenance(result, "income", curr_date)


def get_balance_sheet(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str | None = None,
    *,
    information_frontier: str | None = None,
) -> str:
    """J-Quants balance-sheet summary + curated yfinance line items."""
    base = jqf.get_balance_sheet(
        ticker,
        freq,
        curr_date,
        **_frontier_kwargs(information_frontier),
    )
    result = base + _no_date_live_note(curr_date) + _detail_block(ticker, "balance", freq, curr_date)
    return _with_official_provenance(result, "balance", curr_date)


def get_cashflow(
    ticker: str,
    freq: str = "quarterly",
    curr_date: str | None = None,
    *,
    information_frontier: str | None = None,
) -> str:
    """J-Quants cash-flow summary + curated yfinance line items."""
    base = jqf.get_cashflow(
        ticker,
        freq,
        curr_date,
        **_frontier_kwargs(information_frontier),
    )
    result = base + _no_date_live_note(curr_date) + _detail_block(ticker, "cashflow", freq, curr_date)
    return _with_official_provenance(result, "cashflow", curr_date)
