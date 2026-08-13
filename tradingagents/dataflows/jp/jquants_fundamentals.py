"""J-Quants financial summaries (/fins/summary, available on the Light plan).

One endpoint carries a summarized balance sheet, income statement, and cash-flow
statement per disclosed period, so it backs all four fundamental tools. Full
line-item statements (J-Quants /fins/details) are Premium-only; EDINET XBRL is a
possible later enhancement for that detail.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradingagents.provenance import (
    SourceInterval,
    SourceObservation,
    SourceWatermark,
    attach_source_observations,
    attach_source_watermarks,
)

from ..symbol_utils import NoMarketDataError
from .jquants_common import (
    from_jquants_code,
    memoized_fetch,
    parse_number as _num,
    to_jquants_code,
)

# How many recent disclosed periods to show in each statement.
_PERIOD_LIMIT = 4
# A listed Japanese issuer normally reports at least semi-annually. A snapshot
# older than this cannot prove that currently relevant fundamentals were
# observed, so coverage must fail closed rather than imply "unchanged".
_MAX_DISCLOSURE_AGE_DAYS = 180
_TOKYO = ZoneInfo("Asia/Tokyo")
_NUMERIC_FIELDS = (
    "Sales",
    "OP",
    "OdP",
    "NP",
    "EPS",
    "BPS",
    "TA",
    "Eq",
    "CFO",
    "CFI",
    "CFF",
    "CashEq",
    "ShOutFY",
    "TrShFY",
    "DivAnn",
    "PayoutRatioAnn",
    "EqAR",
    "FEPS",
    "NxFEPS",
)


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


def _visible_summary_records(
    symbol: str,
    curr_date: str | None,
    *,
    information_frontier: str | None = None,
):
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

    excluded_undated = False
    if curr_date:
        # Require a DiscDate <= curr_date: an undated row can't be confirmed to
        # predate curr_date, so excluding it keeps the look-ahead guard sound.
        excluded_undated = any(not record.get("DiscDate") for record in records)
        records = [r for r in records if r.get("DiscDate") and r.get("DiscDate") <= curr_date]
        if not records:
            raise NoMarketDataError(
                symbol, canonical, f"no financial summary disclosed on/before {curr_date}"
            )

    reported_records = len(records)
    excluded_unknown_time = False
    if information_frontier is not None:
        frontier = datetime.fromisoformat(information_frontier)
        if frontier.utcoffset() is None:
            raise ValueError("J-Quants Information Frontier requires a timezone")
        frontier_day = frontier.astimezone(_TOKYO).date().isoformat()
        excluded_unknown_time = any(
            record.get("DiscDate") == frontier_day and _precise_available_at(record) is None
            for record in records
        )
        records = [
            record
            for record in records
            if (
                (available_at := _precise_available_at(record)) is not None
                and datetime.fromisoformat(available_at) <= frontier
            )
            or (
                available_at is None
                and bool(record.get("DiscDate"))
                and record["DiscDate"] < frontier_day
            )
        ]
        if not records:
            raise NoMarketDataError(
                symbol,
                canonical,
                "no financial summary was available at the Information Frontier",
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
    return canonical, ordered, excluded_undated, excluded_unknown_time, reported_records


def _fetch_summary_periods(
    symbol: str,
    curr_date: str | None,
    *,
    information_frontier: str | None = None,
):
    canonical, ordered, _excluded_undated, _excluded_unknown_time, _reported_records = (
        _visible_summary_records(
            symbol,
            curr_date,
            information_frontier=information_frontier,
        )
    )
    return canonical, _dedupe_periods(ordered)


def _comparison_key(canonical: str, record: dict) -> str:
    parts = (
        canonical.removesuffix(".T"),
        str(record.get("CurPerType") or "unknown"),
        str(record.get("CurPerEn") or record.get("CurFYEn") or "unknown"),
    )
    return "jquants-fundamental:" + ":".join(parts)


def _record_identity(canonical: str, record: dict) -> str:
    return _comparison_key(canonical, record)


def _native_record_identity(record: dict) -> str | None:
    disclosure_number = record.get("DiscNo") or record.get("DisclosureNumber")
    if disclosure_number:
        return str(disclosure_number)
    return None


def _record_version(record: dict) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "jquants-fundamental:" + hashlib.sha256(payload.encode()).hexdigest()[:20]


def _accounting_scope(record: dict) -> str:
    return _reporting_basis(record).casefold().replace(", ", ":").replace(" ", "-")


def _is_correction(record: dict) -> bool:
    value = (
        str(
            record.get("CorrectionFlag")
            or record.get("CorrectionFlg")
            or record.get("IsCorrection")
            or ""
        )
        .strip()
        .casefold()
    )
    return value in {"1", "true", "yes", "y"}


def _is_true(record: dict, *keys: str) -> bool:
    return any(
        str(record.get(key) or "").strip().casefold() in {"1", "true", "yes", "y"} for key in keys
    )


def _explicit_restatement(record: dict) -> bool:
    return _is_true(record, "RetrospectiveRestatement", "RetroRest")


def _explicit_accounting_change(record: dict) -> bool:
    return _is_true(
        record,
        "SignificantChangesInTheScopeOfConsolidation",
        "ChangesBasedOnRevisionsOfAccountingStandard",
        "ChangesOtherThanOnesBasedOnRevisionsOfAccountingStandard",
        "ChangesInAccountingEstimates",
        "SigChgInScopeOfCons",
        "ChgBasedRevOfAccStd",
        "ChgOtherRevOfAccStd",
        "ChgInAccEst",
    )


def _changed_numeric_values(previous: dict, current: dict) -> bool:
    return any(previous.get(key) != current.get(key) for key in _NUMERIC_FIELDS)


def _available_at(record: dict) -> str:
    day = str(record.get("DiscDate") or "")
    raw_time = str(record.get("DiscTime") or "23:59:59")
    try:
        return datetime.fromisoformat(f"{day}T{raw_time}").replace(tzinfo=_TOKYO).isoformat()
    except ValueError:
        return datetime.fromisoformat(f"{day}T23:59:59").replace(tzinfo=_TOKYO).isoformat()


def _precise_available_at(record: dict) -> str | None:
    day = record.get("DiscDate")
    raw_time = record.get("DiscTime")
    if not day or not raw_time:
        return None
    try:
        return datetime.fromisoformat(f"{day}T{raw_time}").replace(tzinfo=_TOKYO).isoformat()
    except ValueError:
        return None


def _snapshot_metadata(
    canonical: str,
    records: list[dict],
    curr_date: str | None,
    *,
    excluded_undated: bool = False,
    excluded_unknown_time: bool = False,
    information_frontier: str | None = None,
    reported_records: int | None = None,
):
    grouped: dict[str, list[dict]] = defaultdict(list)
    dated_records = [record for record in records if record.get("DiscDate")]
    for record in reversed(dated_records):
        grouped[_comparison_key(canonical, record)].append(record)
    observations = []
    for comparison_key, versions in grouped.items():
        previous = None
        previous_version = None
        for record in versions:
            version_id = _record_version(record)
            record_id = _record_identity(canonical, record)
            scope = _accounting_scope(record)
            correction = _is_correction(record)
            if previous is None:
                hint = "new_filing"
            elif _accounting_scope(previous) != scope or _explicit_accounting_change(record):
                hint = "accounting_scope_change"
            elif _explicit_restatement(record):
                hint = "restatement"
            elif correction:
                hint = "correction"
            elif _changed_numeric_values(previous, record):
                hint = "unclassifiable"
            else:
                hint = "unclassifiable"
            observations.append(
                SourceObservation(
                    source="J-Quants fundamentals",
                    record_id=record_id,
                    version_id=version_id,
                    status=(
                        "corrected"
                        if correction or hint in {"correction", "restatement"}
                        else "published"
                    ),
                    published_at=(
                        f"{record.get('DiscDate')} {record.get('DiscTime') or ''}".strip()
                    ),
                    available_at=_available_at(record),
                    availability_basis=(
                        "official disclosure date and time"
                        if _precise_available_at(record) is not None
                        else "official disclosure date; conservative end-of-day availability"
                    ),
                    title=(
                        f"{record.get('CurPerType') or 'Financial summary'} "
                        f"ending {record.get('CurPerEn') or record.get('CurFYEn') or '?'}"
                    ),
                    replaces_version_id=previous_version,
                    record_kind="fundamental",
                    native_record_id=_native_record_identity(record),
                    comparison_key=comparison_key,
                    change_hint=hint,
                    accounting_scope=scope,
                )
            )
            previous = record
            previous_version = version_id
    dates = [str(record["DiscDate"]) for record in dated_records]
    scan_boundary = curr_date or datetime.now(_TOKYO).date().isoformat()
    limitations = []
    if len(dated_records) != len(records) or excluded_undated:
        limitations.append("Rows without a disclosure date were excluded from the PIT snapshot.")
    if excluded_unknown_time:
        limitations.append(
            "Rows without a precise disclosure time were excluded at the Information Frontier."
        )
    if dates:
        newest_disclosure = datetime.strptime(max(dates), "%Y-%m-%d").date()
        cutoff = datetime.strptime(scan_boundary, "%Y-%m-%d").date()
        if cutoff - newest_disclosure > timedelta(days=_MAX_DISCLOSURE_AGE_DAYS):
            limitations.append(
                "Latest visible disclosure is older than 180 days at the analysis cutoff."
            )
    limitations = tuple(limitations)
    watermark = SourceWatermark(
        source="J-Quants fundamentals",
        scanned_start=min(dates) if dates else scan_boundary,
        scanned_end=scan_boundary,
        status="limited" if limitations else "complete",
        limitations=limitations,
        returned_records=len(dated_records),
        reported_records=(len(records) if reported_records is None else reported_records),
        requested_interval=SourceInterval(start=scan_boundary, end=scan_boundary),
        limitation_kind="partial" if limitations else None,
        information_frontier=information_frontier,
    )
    return tuple(observations), watermark


def _fetch_summary_snapshot(
    symbol: str,
    curr_date: str | None,
    *,
    information_frontier: str | None = None,
):
    canonical, records, excluded_undated, excluded_unknown_time, reported_records = (
        _visible_summary_records(
            symbol,
            curr_date,
            information_frontier=information_frontier,
        )
    )
    observations, watermark = _snapshot_metadata(
        canonical,
        records,
        curr_date,
        excluded_undated=excluded_undated,
        excluded_unknown_time=excluded_unknown_time,
        information_frontier=information_frontier,
        reported_records=reported_records,
    )
    return canonical, _dedupe_periods(records), observations, watermark


def _attach_snapshot_metadata(text: str, observations, watermark: SourceWatermark) -> str:
    return attach_source_watermarks(attach_source_observations(text, *observations), watermark)


def fetch_periods(
    ticker: str,
    curr_date: str | None = None,
    *,
    information_frontier: str | None = None,
):
    """Public accessor: ``(canonical, records)`` for ``ticker``, newest disclosure
    first, already look-ahead filtered (``DiscDate <= curr_date``).

    Exposed for the JP fundamentals assembler (``jp_fundamentals``), which reads
    the raw summary fields (shares, dividends, forecasts, quarterly cumulatives)
    to compute valuation ratios. Keeping the fetch/filter/sort here — and the
    ratio math in the assembler — leaves this vendor responsible only for
    official data, not derived metrics. Raises ``NoMarketDataError`` when nothing
    is disclosed on/before ``curr_date``.
    """
    return _fetch_summary_periods(
        ticker,
        curr_date,
        information_frontier=information_frontier,
    )


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
        for label, spec in field_specs:
            value = _fmt(spec(r)) if callable(spec) else _fmt_field(r, spec)
            parts.append(f"{label}={value}")
        lines.append(f"- {_period_label(r)}: " + ", ".join(parts))
    return "\n".join(lines)


def get_fundamentals(
    ticker: str,
    curr_date: str | None = None,
    *,
    information_frontier: str | None = None,
) -> str:
    """Headline fundamentals overview from the latest disclosed period."""
    canonical, records, observations, watermark = _fetch_summary_snapshot(
        ticker,
        curr_date,
        information_frontier=information_frontier,
    )
    r = records[0]
    body = "\n".join(
        [
            f"# Fundamentals overview for {canonical} (J-Quants summary)",
            f"Latest disclosure: {r.get('DocType', '?')} — {_period_label(r)}",
            f"Reporting basis: {_reporting_basis(r)}",
            f"Net sales: {_fmt(r.get('Sales'))}",
            f"Operating profit: {_fmt_field(r, 'OP')}    Ordinary profit: {_fmt_field(r, 'OdP')}",
            f"Net profit: {_fmt(r.get('NP'))}",
            f"EPS: {_fmt(r.get('EPS'))}    BPS: {_fmt(r.get('BPS'))}",
            f"Total assets: {_fmt(r.get('TA'))}    Net assets: {_fmt(r.get('Eq'))}",
            f"Cash flows — operating: {_fmt(r.get('CFO'))}, investing: {_fmt(r.get('CFI'))}, "
            f"financing: {_fmt(r.get('CFF'))}",
            f"Cash & equivalents (period end): {_fmt(r.get('CashEq'))}",
        ]
    )
    return _attach_snapshot_metadata(body, observations, watermark)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """Balance-sheet summary (total assets, derived liabilities, net assets)."""
    canonical, records, observations, watermark = _fetch_summary_snapshot(ticker, curr_date)
    body = _render_periods(
        canonical,
        records,
        freq,
        "Balance sheet summary",
        [("TotalAssets", "TA"), ("TotalLiabilities", _liabilities), ("NetAssets", "Eq")],
    )
    return _attach_snapshot_metadata(body, observations, watermark)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """Cash-flow summary (operating/investing/financing + period-end cash)."""
    canonical, records, observations, watermark = _fetch_summary_snapshot(ticker, curr_date)
    body = _render_periods(
        canonical,
        records,
        freq,
        "Cash flow summary",
        [("Operating", "CFO"), ("Investing", "CFI"), ("Financing", "CFF"), ("CashEnd", "CashEq")],
    )
    return _attach_snapshot_metadata(body, observations, watermark)


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    """Income-statement summary (sales, operating/ordinary/net profit, EPS, BPS)."""
    canonical, records, observations, watermark = _fetch_summary_snapshot(ticker, curr_date)
    body = _render_periods(
        canonical,
        records,
        freq,
        "Income statement summary",
        [
            ("NetSales", "Sales"),
            ("OperatingProfit", "OP"),
            ("OrdinaryProfit", "OdP"),
            ("NetProfit", "NP"),
            ("EPS", "EPS"),
            ("BPS", "BPS"),
        ],
    )
    return _attach_snapshot_metadata(body, observations, watermark)
