"""China company-profile and disclosure-safe fundamental-data assembler."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import pandas as pd

from tradingagents.provenance import (
    ProvenanceRecord,
    attach_evidence_span,
    attach_provenance,
)

from ..errors import NoMarketDataError, VendorRateLimitError
from ..lookahead import is_near_live
from ..rate_limit import stop_on_rate_limit_requested
from ..y_finance import get_fundamentals as get_yfinance_fundamentals
from .common import canonical_a_share
from .company import classify_entity, get_company_profile_snapshot
from .sina_finance import (
    fetch_finance_records,
    filter_visible_records,
    validate_analysis_date,
)

logger = logging.getLogger(__name__)

_PROFILE_FIELDS = (
    "公司名称",
    "A股简称",
    "所属市场",
    "所属行业",
    "成立日期",
    "上市日期",
    "注册资金",
    "官方网站",
    "主营业务",
    "经营范围",
)

# These are direct source-label mappings, not derived formulas. Financial firms
# deliberately use their own metric set so manufacturer ratios are not imputed.
_ABSTRACT_FIELDS = {
    "general": (
        ("Basic EPS", ("基本每股收益", "每股收益")),
        ("Book value per share", ("每股净资产",)),
        ("Revenue", ("营业总收入", "营业收入")),
        ("Revenue growth", ("营业总收入同比增长", "营业收入同比增长")),
        ("Net income attributable to parent", ("归属净利润", "归属于母公司股东的净利润")),
        ("Net income growth", ("归属净利润同比增长", "净利润同比增长")),
        ("Gross margin", ("销售毛利率", "毛利率")),
        ("Net margin", ("销售净利率", "净利率")),
        ("ROE", ("净资产收益率", "加权净资产收益率")),
        ("ROA", ("总资产收益率",)),
        ("Debt ratio", ("资产负债率",)),
        ("Current ratio", ("流动比率",)),
        ("Quick ratio", ("速动比率",)),
        ("Operating cash flow per share", ("每股经营现金流", "每股经营活动产生的现金流量净额")),
    ),
    "financial": (
        ("Basic EPS", ("基本每股收益", "每股收益")),
        ("Book value per share", ("每股净资产",)),
        ("Operating income", ("营业总收入", "营业收入")),
        ("Net income attributable to parent", ("归属净利润", "归属于母公司股东的净利润")),
        ("ROE", ("净资产收益率", "加权净资产收益率")),
        ("ROA", ("总资产收益率",)),
        ("Net interest margin", ("净息差", "净利差")),
        ("Non-performing loan ratio", ("不良贷款率",)),
        ("Capital adequacy ratio", ("资本充足率",)),
        ("Core tier-1 capital adequacy ratio", ("核心一级资本充足率",)),
        ("Provision coverage ratio", ("拨备覆盖率",)),
        ("Solvency adequacy ratio", ("综合偿付能力充足率", "偿付能力充足率")),
    ),
}

_META_COLUMNS = {
    "ReportDate",
    "PublishDate",
    "UpdateDate",
    "VisibilityDate",
    "DataSource",
    "Audited",
    "Currency",
    "StatementType",
}


def _normalized_label(value: object) -> str:
    return re.sub(r"[\s（）()：:、,，%％]+", "", str(value)).casefold()


def _find_column(columns, aliases: tuple[str, ...]) -> str | None:
    candidates = [(column, _normalized_label(column)) for column in columns]
    for alias in map(_normalized_label, aliases):
        exact = next((column for column, value in candidates if value == alias), None)
        if exact is not None:
            return exact
    return None


def _render_profile(profile: pd.DataFrame) -> str:
    if profile.empty:
        return "## Company profile (CNINFO)\nUnavailable."
    row = profile.iloc[0]
    lines = ["## Company profile (CNINFO; current reference, not historical PIT)"]
    for field in _PROFILE_FIELDS:
        value = row.get(field)
        if pd.notna(value) and str(value).strip():
            lines.append(f"{field}: {value}")
    return "\n".join(lines)


def _render_abstract(frame: pd.DataFrame, entity_type: str) -> tuple[str, list[str]]:
    output = pd.DataFrame(
        {
            "ReportDate": frame["ReportDate"].dt.strftime("%Y-%m-%d"),
            "PublishDate": frame["PublishDate"].dt.strftime("%Y-%m-%d"),
            "UpdateDate": frame["UpdateDate"].dt.strftime("%Y-%m-%d"),
            "VisibilityDate": frame["VisibilityDate"].dt.strftime("%Y-%m-%d"),
        }
    )
    missing: list[str] = []
    columns = [column for column in frame.columns if column not in _META_COLUMNS]
    for label, aliases in _ABSTRACT_FIELDS[entity_type]:
        column = _find_column(columns, aliases)
        if column is None or not frame[column].notna().any():
            output[label] = "N/A"
            missing.append(label)
        else:
            output[label] = frame[column]
    return output.fillna("N/A").to_csv(index=False), missing


def _live_yfinance_block(ticker: str, curr_date: str | None) -> str:
    if curr_date is not None and not is_near_live(curr_date, ticker):
        return attach_evidence_span(
            attach_provenance(
                "## Current valuation and analyst snapshot (yfinance)\n"
                "Not requested: current-only valuation and forecasts are excluded from "
                f"historical analysis dated {curr_date}.",
                ProvenanceRecord(
                    evidence="get_fundamentals",
                    source="yfinance current valuation snapshot",
                    requested=curr_date,
                    effective="—",
                    timing="live-only; not queried for historical analysis",
                ),
            ),
            temporal_scope="live_only",
        )
    try:
        result = get_yfinance_fundamentals(ticker, curr_date)
    except VendorRateLimitError:
        if stop_on_rate_limit_requested():
            raise
        logger.warning("CN fundamentals: live yfinance snapshot rate-limited for %s", ticker)
        result = ""
    except Exception as exc:  # noqa: BLE001 - optional enrichment
        logger.warning("CN fundamentals: live yfinance snapshot failed for %s: %s", ticker, exc)
        result = ""
    retrieved = datetime.now(UTC).isoformat(timespec="seconds")
    if not result or result.startswith("Error retrieving fundamentals"):
        body = "## Current valuation and analyst snapshot (yfinance)\nUnavailable."
        effective = "—"
        timing = "live retrieval unavailable"
    else:
        body = "## Current valuation and analyst snapshot (yfinance)\n" + result
        effective = curr_date or retrieved[:10]
        timing = "current-only snapshot; not historical PIT"
    return attach_evidence_span(
        attach_provenance(
            body,
            ProvenanceRecord(
                evidence="get_fundamentals",
                source="yfinance current valuation snapshot",
                requested=curr_date or retrieved[:10],
                effective=effective,
                timing=timing,
                retrieved_at=retrieved,
            ),
        ),
        temporal_scope="live_only",
    )


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Assemble CNINFO profile, disclosure-filtered metrics, and live valuation."""
    validate_analysis_date(curr_date)
    canonical, _code, _exchange = canonical_a_share(ticker)
    profile_issue: str | None = None
    profile_retrieved_at: str | None = None
    abstract_issue: str | None = None
    if curr_date is not None and not is_near_live(curr_date, ticker):
        profile = pd.DataFrame()
        profile_issue = (
            "live-only company profile not queried for historical or future date"
        )
    else:
        try:
            profile_snapshot = get_company_profile_snapshot(ticker)
            profile = profile_snapshot.frame
            profile_retrieved_at = profile_snapshot.retrieved_at
            if profile.empty:
                profile_issue = "no company profile returned"
        except VendorRateLimitError:
            if stop_on_rate_limit_requested():
                raise
            logger.warning("CN fundamentals: CNINFO profile rate-limited for %s", ticker)
            profile = pd.DataFrame()
            profile_issue = "rate limited"
        except Exception as exc:  # noqa: BLE001 - partial assembler result is useful
            logger.warning(
                "CN fundamentals: CNINFO profile failed for %s: %s",
                ticker,
                exc,
            )
            profile = pd.DataFrame()
            profile_issue = f"{type(exc).__name__}: {exc}"

    try:
        _canonical, raw = fetch_finance_records(ticker, "abstract")
        abstract = filter_visible_records(raw, curr_date, "quarterly")
        if abstract.empty:
            abstract_issue = "no reports visible for the requested date"
    except VendorRateLimitError:
        if stop_on_rate_limit_requested():
            raise
        logger.warning("CN fundamentals: Sina abstract rate-limited for %s", ticker)
        abstract = pd.DataFrame()
        abstract_issue = "rate limited"
    except Exception as exc:  # noqa: BLE001 - partial assembler result is useful
        logger.warning("CN fundamentals: Sina abstract failed for %s: %s", ticker, exc)
        abstract = pd.DataFrame()
        abstract_issue = f"{type(exc).__name__}: {exc}"

    if profile.empty and abstract.empty:
        profile_detail = profile_issue or "unavailable"
        abstract_detail = abstract_issue or "unavailable"
        raise NoMarketDataError(
            ticker,
            canonical,
            f"CNINFO profile: {profile_detail}; Sina financial abstract: {abstract_detail}",
            availability_notes=(
                f"- CNINFO company profile unavailable ({profile_detail}).",
                f"- Sina financial abstract unavailable ({abstract_detail}).",
            ),
        )

    populated_fields = abstract.drop(columns=_META_COLUMNS, errors="ignore").dropna(
        axis=1, how="all"
    )
    entity_type = classify_entity(profile, populated_fields.columns)
    requested = curr_date or "not provided (live retrieval)"
    if profile.empty:
        profile_body = (
            f"# Entity mapping: {entity_type}\n\n"
            f"{_render_profile(profile)}\n\n"
            "CNINFO profile source status: unavailable."
        )
        profile_record = ProvenanceRecord(
            evidence="get_fundamentals",
            source="AkShare / CNINFO company profile",
            requested=requested,
            effective="—",
            timing=(
                "live-only; not queried for historical or future analysis"
                if profile_issue
                and profile_issue.startswith("live-only")
                else f"live-only retrieval unavailable: {profile_issue or 'no data'}"
            ),
            retrieved_at=profile_retrieved_at,
        )
    else:
        profile_body = (
            f"# Entity mapping: {entity_type}\n\n"
            f"{_render_profile(profile)}"
        )
        profile_record = ProvenanceRecord(
            evidence="get_fundamentals",
            source="AkShare / CNINFO company profile",
            requested=requested,
            effective="current reference",
            timing="live-only current company reference; not historical PIT",
            retrieved_at=profile_retrieved_at,
        )
    profile_block = attach_evidence_span(
        attach_provenance(profile_body, profile_record),
        temporal_scope="live_only",
    )

    if abstract.empty:
        abstract_body = (
            f"# China A-share Fundamentals for {canonical}\n"
            f"# Requested analysis date: {requested}\n\n"
            "## Financial abstract (AkShare / Sina)\n"
            "Unavailable for the requested date."
        )
        abstract_record = ProvenanceRecord(
            evidence="get_fundamentals",
            source="AkShare / Sina financial abstract",
            requested=requested,
            effective="—",
            timing=f"retrieval unavailable: {abstract_issue or 'no data'}",
        )
    else:
        table, missing = _render_abstract(abstract, entity_type)
        effective = abstract["VisibilityDate"].max().strftime("%Y-%m-%d")
        abstract_body = "\n".join(
            (
                f"# China A-share Fundamentals for {canonical}",
                f"# Requested analysis date: {requested}",
                "",
                "## Financial abstract (AkShare / Sina)",
                "Visibility rule: max(report date, publication date, update date) <= cutoff.",
                f"Latest visible disclosure/update: {effective}",
                f"Effective period: {abstract['ReportDate'].max().strftime('%Y-%m-%d')}",
                f"Missing mapped fields: {', '.join(missing) if missing else 'none'}",
                table,
            )
        )
        abstract_record = ProvenanceRecord(
            evidence="get_fundamentals",
            source="AkShare / Sina financial abstract",
            requested=requested,
            effective=effective,
            timing="publication/update-date filtered; later conflicting date wins",
        )

    abstract_block = attach_evidence_span(
        attach_provenance(abstract_body, abstract_record),
        temporal_scope="point_in_time",
    )
    base = f"{profile_block}\n\n{abstract_block}"
    return base + "\n\n" + _live_yfinance_block(ticker, curr_date)
