"""China statement assembler: disclosure-filtered Sina base + yfinance supplement."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import pandas as pd

from tradingagents.provenance import ProvenanceRecord, attach_provenance

from ..errors import NoMarketDataError, VendorError
from ..y_finance import get_statement_frame
from .company import classify_entity, get_company_profile
from .sina_finance import (
    fetch_finance_records,
    filter_visible_records,
    validate_analysis_date,
)

logger = logging.getLogger(__name__)

_EVIDENCE = {
    "income": "get_income_statement",
    "balance": "get_balance_sheet",
    "cashflow": "get_cashflow",
}
_TITLE = {
    "income": "Income Statement",
    "balance": "Balance Sheet",
    "cashflow": "Cash Flow Statement",
}

# Values are source-label aliases, not formulas. Financial-company mappings are
# deliberately separate so a bank never receives manufacturer-only ratios/lines.
_FIELDS = {
    "income": {
        "general": (
            ("Revenue", ("营业收入", "营业总收入")),
            ("Operating cost", ("营业成本", "营业总成本")),
            ("Gross profit", ("营业毛利",)),
            ("Selling expense", ("销售费用",)),
            ("Administrative expense", ("管理费用",)),
            ("R&D expense", ("研发费用",)),
            ("Finance expense", ("财务费用",)),
            ("Operating profit", ("营业利润",)),
            ("Pretax profit", ("利润总额",)),
            ("Net income", ("净利润",)),
            (
                "Net income attributable to parent",
                ("归属于母公司股东的净利润", "归属于母公司所有者的净利润"),
            ),
            ("Basic EPS", ("基本每股收益",)),
        ),
        "financial": (
            ("Operating income", ("营业收入", "营业总收入")),
            ("Net interest income", ("利息净收入",)),
            ("Net fee and commission income", ("手续费及佣金净收入",)),
            (
                "Premium income",
                (
                    "已赚保费",
                    "保险业务收入",
                ),
            ),
            ("Investment income", ("投资收益",)),
            ("Operating and administrative expense", ("业务及管理费",)),
            ("Credit impairment loss", ("信用减值损失", "资产减值损失")),
            ("Operating profit", ("营业利润",)),
            ("Pretax profit", ("利润总额",)),
            ("Net income", ("净利润",)),
            (
                "Net income attributable to parent",
                ("归属于母公司股东的净利润", "归属于母公司所有者的净利润"),
            ),
            ("Basic EPS", ("基本每股收益",)),
        ),
    },
    "balance": {
        "general": (
            ("Cash", ("货币资金",)),
            ("Trading financial assets", ("交易性金融资产",)),
            ("Accounts receivable", ("应收账款",)),
            ("Inventory", ("存货",)),
            ("Current assets", ("流动资产合计",)),
            ("Property, plant and equipment", ("固定资产", "固定资产净额")),
            ("Total assets", ("资产总计",)),
            ("Short-term borrowings", ("短期借款",)),
            ("Accounts payable", ("应付账款",)),
            ("Current liabilities", ("流动负债合计",)),
            ("Long-term borrowings", ("长期借款",)),
            ("Total liabilities", ("负债合计",)),
            (
                "Equity attributable to parent",
                ("归属于母公司股东权益合计", "归属于母公司所有者权益合计"),
            ),
            ("Total equity", ("所有者权益合计", "股东权益合计")),
        ),
        "financial": (
            ("Cash and central-bank deposits", ("现金及存放中央银行款项",)),
            ("Interbank placements", ("存放同业款项", "存放同业和其它金融机构款项")),
            ("Loans and advances", ("发放贷款及垫款",)),
            (
                "Financial investments",
                (
                    "金融投资",
                    "交易性金融资产",
                ),
            ),
            ("Total assets", ("资产总计",)),
            ("Central-bank borrowings", ("向中央银行借款",)),
            ("Interbank deposits", ("同业及其他金融机构存放款项",)),
            ("Customer deposits", ("吸收存款",)),
            ("Policyholder deposits/investment contracts", ("保户储金及投资款",)),
            ("Total liabilities", ("负债合计",)),
            ("Share capital", ("股本", "实收资本(或股本)")),
            (
                "Equity attributable to parent",
                ("归属于母公司股东权益合计", "归属于母公司所有者权益合计"),
            ),
            ("Total equity", ("所有者权益合计", "股东权益合计")),
        ),
    },
    "cashflow": {
        "general": (
            ("Cash received from customers", ("销售商品、提供劳务收到的现金",)),
            ("Cash paid to suppliers", ("购买商品、接受劳务支付的现金",)),
            ("Cash paid to employees", ("支付给职工以及为职工支付的现金",)),
            ("Net operating cash flow", ("经营活动产生的现金流量净额",)),
            ("Capital expenditure", (
                "购建固定资产、无形资产和其他长期资产支付的现金",
                "购建固定资产、无形资产和其他长期资产所支付的现金",
            )),
            ("Net investing cash flow", ("投资活动产生的现金流量净额",)),
            ("Borrowing proceeds", ("取得借款收到的现金",)),
            ("Dividends and interest paid", (
                "分配股利、利润或偿付利息支付的现金",
                "分配股利、利润或偿付利息所支付的现金",
            )),
            ("Net financing cash flow", ("筹资活动产生的现金流量净额",)),
            ("Net increase in cash", ("现金及现金等价物净增加额",)),
            ("Ending cash balance", ("期末现金及现金等价物余额",)),
        ),
        "financial": (
            ("Net customer/interbank deposits", ("客户存款和同业存放款项净增加额",)),
            ("Net central-bank borrowings", ("向中央银行借款净增加额",)),
            ("Interest, fees and commissions received", ("收取利息、手续费及佣金的现金",)),
            ("Net operating cash flow", ("经营活动产生的现金流量净额",)),
            ("Net investing cash flow", ("投资活动产生的现金流量净额",)),
            ("Net financing cash flow", ("筹资活动产生的现金流量净额",)),
            ("Net increase in cash", ("现金及现金等价物净增加额",)),
            ("Ending cash balance", ("期末现金及现金等价物余额",)),
        ),
    },
}

_YF_ROWS = {
    "income": (
        "Total Revenue",
        "Gross Profit",
        "Operating Income",
        "Pretax Income",
        "Net Income",
        "Net Income Common Stockholders",
        "Basic EPS",
    ),
    "balance": (
        "Cash And Cash Equivalents",
        "Accounts Receivable",
        "Inventory",
        "Current Assets",
        "Total Assets",
        "Current Liabilities",
        "Total Debt",
        "Total Liabilities Net Minority Interest",
        "Stockholders Equity",
    ),
    "cashflow": (
        "Operating Cash Flow",
        "Capital Expenditure",
        "Investing Cash Flow",
        "Financing Cash Flow",
        "Free Cash Flow",
        "End Cash Position",
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
    return re.sub(r"[\s（）()：:、,，]+", "", str(value)).casefold()


def _find_column(columns, aliases: tuple[str, ...]) -> str | None:
    candidates = [(column, _normalized_label(column)) for column in columns]
    normalized_aliases = [_normalized_label(alias) for alias in aliases]
    for alias in normalized_aliases:
        exact = next((column for column, value in candidates if value == alias), None)
        if exact is not None:
            return exact
    return None


def _render_sina_table(
    frame: pd.DataFrame,
    kind: str,
    entity_type: str,
) -> tuple[str, list[str]]:
    fields = _FIELDS[kind][entity_type]
    output = pd.DataFrame(
        {
            "ReportDate": frame["ReportDate"].dt.strftime("%Y-%m-%d"),
            "PublishDate": frame["PublishDate"].dt.strftime("%Y-%m-%d"),
            "UpdateDate": frame["UpdateDate"].dt.strftime("%Y-%m-%d"),
            "VisibilityDate": frame["VisibilityDate"].dt.strftime("%Y-%m-%d"),
            "Currency": frame.get("Currency", "N/A"),
            "Audited": frame.get("Audited", "N/A"),
        }
    )
    missing: list[str] = []
    data_columns = [column for column in frame.columns if column not in _META_COLUMNS]
    for label, aliases in fields:
        column = _find_column(data_columns, aliases)
        if column is None or not frame[column].notna().any():
            output[label] = "N/A"
            missing.append(label)
        else:
            output[label] = frame[column]
    output = output.fillna("N/A")
    return output.to_csv(index=False), missing


def _yfinance_supplement(
    ticker: str,
    kind: str,
    freq: str,
    curr_date: str | None,
    *,
    needed: bool,
) -> str:
    if not needed:
        return ""
    try:
        frame = get_statement_frame(ticker, kind, freq, curr_date)
    except Exception as exc:  # noqa: BLE001 - supplement never hides Sina base
        logger.warning("CN statements: yfinance supplement failed for %s: %s", ticker, exc)
        frame = None
    requested = curr_date or "not provided (live retrieval)"
    evidence = _EVIDENCE[kind]
    if frame is None:
        return attach_provenance(
            "",
            ProvenanceRecord(
                evidence=evidence,
                source="yfinance statement supplement",
                requested=requested,
                effective="—",
                timing="retrieval unavailable",
            ),
        )
    rows = [row for row in _YF_ROWS[kind] if row in frame.index]
    if not rows:
        return attach_provenance(
            "",
            ProvenanceRecord(
                evidence=evidence,
                source="yfinance statement supplement",
                requested=requested,
                effective="—",
                timing="available; no curated line items matched",
            ),
        )
    sub = frame.loc[rows].dropna(axis=1, how="all").dropna(axis=0, how="all")
    if sub.empty:
        return attach_provenance(
            "",
            ProvenanceRecord(
                evidence=evidence,
                source="yfinance statement supplement",
                requested=requested,
                effective="—",
                timing="available; curated line items contained no values",
            ),
        )
    retrieved = datetime.now(UTC).isoformat(timespec="seconds")
    block = (
        "\n\n## Supplemental line items (yfinance)\n"
        f"Requested analysis date: {requested}\n"
        f"Retrieval timestamp: {retrieved}\n"
        "Non-strict PIT: values are filtered only by fiscal period end and may "
        "contain later revisions; publication timestamps are unavailable.\n" + sub.to_csv()
    )
    return attach_provenance(
        block,
        ProvenanceRecord(
            evidence=evidence,
            source="yfinance statement supplement",
            requested=requested,
            effective="fiscal period ends only",
            timing="non-strict PIT; may include later revisions",
            retrieved_at=retrieved,
        ),
    )


def _statement(
    ticker: str,
    kind: str,
    freq: str,
    curr_date: str | None,
) -> str:
    validate_analysis_date(curr_date)
    try:
        canonical, raw = fetch_finance_records(ticker, kind)
    except VendorError as exc:
        canonical = getattr(exc, "canonical", ticker)
        detail = getattr(exc, "detail", "") or f"{type(exc).__name__}: {exc}"
        existing_notes = getattr(exc, "availability_notes", ())
        note = f"- AkShare / Sina {_TITLE[kind]} unavailable ({detail})."
        raise NoMarketDataError(
            ticker,
            canonical,
            f"Sina {_TITLE[kind]} primary source unavailable: {detail}",
            availability_notes=(*existing_notes, note),
        ) from exc
    visible = filter_visible_records(raw, curr_date, freq)
    if visible.empty:
        requested = curr_date or "latest"
        note = (
            f"- AkShare / Sina {_TITLE[kind]} unavailable "
            f"(no reports visible by analysis date {requested})."
        )
        raise NoMarketDataError(
            ticker,
            canonical,
            f"no {kind} reports visible by analysis date {requested}",
            availability_notes=(note,),
        )
    try:
        profile = get_company_profile(ticker)
    except Exception as exc:  # noqa: BLE001 - statement fields can self-classify
        logger.warning("CN statements: company classification failed for %s: %s", ticker, exc)
        profile = pd.DataFrame()
    populated_fields = visible.drop(columns=_META_COLUMNS, errors="ignore").dropna(
        axis=1, how="all"
    )
    entity_type = classify_entity(profile, populated_fields.columns)
    table, missing = _render_sina_table(visible, kind, entity_type)
    requested = curr_date or "not provided (live retrieval)"
    effective = visible["VisibilityDate"].max().strftime("%Y-%m-%d")
    lines = (
        f"# China A-share {_TITLE[kind]} for {canonical} ({freq})\n"
        f"# Entity mapping: {entity_type}\n"
        f"# Requested analysis date: {requested}\n"
        f"# Visibility rule: max(report date, publication date, update date) <= cutoff\n"
        f"# Latest visible disclosure/update: {effective}\n"
        f"# Missing mapped fields: {', '.join(missing) if missing else 'none'}\n\n" + table
    )
    result = attach_provenance(
        lines,
        ProvenanceRecord(
            evidence=_EVIDENCE[kind],
            source="AkShare / Sina CompanyFinanceService",
            requested=requested,
            effective=f"visibility dates <= {curr_date}" if curr_date else effective,
            timing="publication/update-date filtered; later conflicting date wins",
        ),
    )
    return result + _yfinance_supplement(ticker, kind, freq, curr_date, needed=bool(missing))


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _statement(ticker, "income", freq, curr_date)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _statement(ticker, "balance", freq, curr_date)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _statement(ticker, "cashflow", freq, curr_date)
