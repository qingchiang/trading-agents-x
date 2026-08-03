"""Opt-in live contracts for the key cross-market data paths.

Run explicitly with::

    RUN_LIVE_DATA_TESTS=1 PYTHON_DOTENV_DISABLED=1 uv run --locked pytest -q -m live_data

These checks intentionally assert schema, date boundaries, source audit, and
broad numeric sanity—not exact market values, titles, or result counts. They are
monitoring probes for upstream schema or endpoint drift and never run as part of
the default suite or CI.
"""

from __future__ import annotations

import math
import os
import re
from datetime import date, datetime, timedelta
from io import StringIO

import pandas as pd
import pytest

from tradingagents.dataflows import cn_macro, jp_macro
from tradingagents.dataflows.cn import (
    akshare_stock,
    calendar as cn_calendar,
    cn_sentiment,
    cn_statements,
    company,
    news_sources,
    sina_finance,
)
from tradingagents.dataflows.symbol_utils import normalize_symbol
from tradingagents.dataflows.y_finance import get_YFin_data_online
from tradingagents.provenance import extract_provenance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_data,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_DATA_TESTS") != "1",
        reason="Set RUN_LIVE_DATA_TESTS=1 to run live market-data contracts",
    ),
]

_FINANCE_META_COLUMNS = {
    "ReportDate",
    "PublishDate",
    "UpdateDate",
    "VisibilityDate",
    "DataSource",
    "Audited",
    "Currency",
    "StatementType",
}
_DATED_EVENT_LINE = re.compile(r"(?m)^- (\d{4}-\d{2}-\d{2}):")


def _settled_window(days: int = 30) -> tuple[str, str]:
    """Return a recent window ending before any market's current session."""
    end = date.today() - timedelta(days=2)
    return (end - timedelta(days=days)).isoformat(), end.isoformat()


def _china_window(days: int = 45) -> tuple[str, str, date]:
    requested_end = date.today()
    completed_end = cn_calendar.effective_trade_date(requested_end)
    start = completed_end - timedelta(days=days)
    return start.isoformat(), requested_end.isoformat(), completed_end


def _assert_finite_positive(values: pd.Series) -> None:
    numeric = pd.to_numeric(values, errors="coerce")
    assert numeric.notna().all()
    assert numeric.map(math.isfinite).all()
    assert (numeric > 0).all()


def _assert_ohlcv(result, canonical: str, completed_end: date) -> None:
    assert result.canonical == canonical
    assert result.source == "AkShare / Tencent"
    assert result.fallback_reason is None
    assert result.adjustment == "qfq (forward-adjusted)"
    assert not result.frame.empty
    assert {"Date", "Open", "High", "Low", "Close", "Volume"} <= set(
        result.frame.columns
    )
    latest = pd.to_datetime(result.frame["Date"], errors="coerce").max().date()
    assert latest == completed_end
    assert result.effective_end == completed_end.isoformat()
    for column in ("Open", "High", "Low", "Close"):
        _assert_finite_positive(result.frame[column])


def _assert_finance_frame(frame: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    assert not frame.empty
    assert set(frame.columns) >= _FINANCE_META_COLUMNS
    visible = sina_finance.filter_visible_records(
        frame, cutoff.isoformat(), limit=8
    )
    assert not visible.empty
    assert visible["ReportDate"].notna().all()
    assert visible["VisibilityDate"].notna().all()
    assert visible["VisibilityDate"].dt.date.le(cutoff).all()
    data_columns = [
        column for column in visible.columns if column not in _FINANCE_META_COLUMNS
    ]
    assert data_columns
    assert visible[data_columns].notna().any(axis=None)
    return visible


def _assert_macro_points(data: dict, requested_end: date) -> tuple[date, float]:
    assert data["points"]
    latest_raw, value_raw = data["points"][-1]
    latest = date.fromisoformat(latest_raw)
    value = float(value_raw)
    assert latest <= requested_end
    assert math.isfinite(value)
    return latest, value


def _latest_event_date(text: str) -> date | str:
    dates = [date.fromisoformat(value) for value in _DATED_EVENT_LINE.findall(text)]
    return max(dates) if dates else "empty-window"


def test_us_yfinance_daily_ohlcv_contract(live_endpoint):
    with live_endpoint("us.yfinance.daily", source="yfinance") as audit:
        start, end = _settled_window()
        output = get_YFin_data_online("NVDA", start, end)

        assert "# Actual data source: yfinance" in output
        frame = pd.read_csv(StringIO(output[output.index("Date,") :]))
        assert {"Date", "Open", "High", "Low", "Close", "Volume"} <= set(frame.columns)
        assert not frame.empty
        assert pd.to_datetime(frame["Date"], errors="coerce").notna().all()
        latest = pd.to_datetime(frame["Date"]).max().date()
        audit.observe(source="yfinance", last_observation=latest)
        assert latest <= date.fromisoformat(end)
        assert latest >= date.fromisoformat(end) - timedelta(days=10)
        for column in ("Open", "High", "Low", "Close"):
            _assert_finite_positive(frame[column])


def test_china_wanhua_tencent_qfq_contract(live_endpoint):
    with live_endpoint("cn.tencent.qfq.600309", source="Tencent") as audit:
        start, end, completed_end = _china_window()
        wanhua = akshare_stock.fetch_ohlcv("600309.SS", start, end)
        audit.observe(source=wanhua.source, last_observation=wanhua.effective_end)
        _assert_ohlcv(wanhua, "600309.SS", completed_end)


def test_china_midea_symbol_normalization_and_freshness_contract(live_endpoint):
    with live_endpoint("cn.tencent.qfq.000333", source="Tencent") as audit:
        start, end, completed_end = _china_window()
        assert normalize_symbol("000333") == "000333.SZ"
        assert normalize_symbol("000333.SZ") == "000333.SZ"
        bare = akshare_stock.fetch_ohlcv("000333", start, end)
        explicit = akshare_stock.fetch_ohlcv("000333.SZ", start, end)
        audit.observe(source=bare.source, last_observation=bare.effective_end)
        _assert_ohlcv(bare, "000333.SZ", completed_end)
        _assert_ohlcv(explicit, "000333.SZ", completed_end)
        assert bare.frame.equals(explicit.frame)


def test_china_company_and_sina_finance_contracts(live_endpoint):
    cutoff = date.today()
    with live_endpoint("cn.cninfo.profile.600309", source="CNINFO") as audit:
        profile = company.get_company_profile("600309.SS")
        audit.observe(source="CNINFO", last_observation="current snapshot")
        assert not profile.empty
        assert {
            "公司名称",
            "A股代码",
            "A股简称",
            "所属市场",
            "所属行业",
            "主营业务",
        } <= set(profile.columns)
        assert str(profile.iloc[0]["A股代码"]).zfill(6) == "600309"
        assert str(profile.iloc[0]["公司名称"]).strip()

    for kind in ("abstract", "balance", "income", "cashflow"):
        with live_endpoint(
            f"cn.sina.finance.{kind}.600309", source="Sina Finance"
        ) as audit:
            canonical, frame = sina_finance.fetch_finance_records("600309.SS", kind)
            assert canonical == "600309.SS"
            visible = _assert_finance_frame(frame, cutoff)
            audit.observe(
                source="Sina Finance",
                last_observation=visible["VisibilityDate"].max().date(),
            )


def test_china_bank_profile_and_statement_mapping_contract(live_endpoint):
    cutoff = date.today()
    with live_endpoint("cn.cninfo.profile.600036", source="CNINFO") as audit:
        profile = company.get_company_profile("600036.SS")
        audit.observe(source="CNINFO", last_observation="current snapshot")
        assert not profile.empty
        assert str(profile.iloc[0]["A股代码"]).zfill(6) == "600036"
        assert company.classify_entity(profile) == "financial"

    with live_endpoint(
        "cn.sina.finance.balance.600036", source="Sina Finance"
    ) as audit:
        canonical, frame = sina_finance.fetch_finance_records("600036.SS", "balance")
        assert canonical == "600036.SS"
        visible = _assert_finance_frame(frame, cutoff)
        audit.observe(
            source="Sina Finance",
            last_observation=visible["VisibilityDate"].max().date(),
        )
        entity_type = company.classify_entity(profile, visible.columns)
        rendered, _missing = cn_statements._render_sina_table(
            visible, "balance", entity_type
        )
        mapped = pd.read_csv(StringIO(rendered))

        assert entity_type == "financial"
        bank_fields = {
            "Cash and central-bank deposits",
            "Interbank placements",
            "Loans and advances",
            "Customer deposits",
        }
        assert bank_fields <= set(mapped.columns)
        assert mapped[list(bank_fields)].replace("N/A", pd.NA).notna().any(axis=None)
        assert "Inventory" not in mapped.columns
        assert "Accounts receivable" not in mapped.columns


def test_china_company_news_schema_contracts(live_endpoint):
    end = date.today()
    start = end - timedelta(days=120)
    start_text, end_text = start.isoformat(), end.isoformat()

    with live_endpoint(
        "cn.cninfo.announcements.600309", source="CNINFO"
    ) as audit:
        disclosures = news_sources.disclosure_rows("600309.SS", start_text, end_text)
        latest = max((row["published"].date() for row in disclosures), default="empty-window")
        audit.observe(source="CNINFO", last_observation=latest)
        assert isinstance(disclosures, list)
        for row in disclosures:
            assert {"code", "name", "title", "published", "url"} <= set(row)
            assert row["code"] == "600309"
            assert row["title"].strip()
            assert row["url"].startswith("https://")
            assert isinstance(row["published"], datetime)
            assert row["published"].tzinfo is not None
            assert start <= row["published"].date() <= end

    with live_endpoint(
        "cn.eastmoney.research.600309", source="Eastmoney Research"
    ) as audit:
        research = news_sources.research_rows("600309.SS", start_text, end_text)
        latest = max((row["published"] for row in research), default="empty-window")
        audit.observe(source="Eastmoney Research", last_observation=latest)
        assert isinstance(research, list)
        for row in research:
            assert {
                "code",
                "name",
                "title",
                "published",
                "institution",
                "rating",
                "rating_change",
                "target_low",
                "target_high",
                "url",
            } <= set(row)
            assert row["code"] == "600309"
            assert row["title"].strip()
            assert isinstance(row["published"], date)
            assert start <= row["published"] <= end


def test_china_research_signal_source_contract(live_endpoint):
    end = date.today()
    with live_endpoint(
        "cn.research-signal.600519", source="Sina Finance -> Eastmoney Research"
    ) as audit:
        result = cn_sentiment.get_research_signal("600519.SS", end.isoformat())
        records = extract_provenance(result)
        assert records
        sources = " -> ".join(record.source for record in records)
        audit.observe(source=sources, last_observation=_latest_event_date(result))
        assert all(record.effective != "unknown" for record in records)
        assert any("unavailable" not in record.timing for record in records)
        eastmoney = [record for record in records if record.source == "Eastmoney Research"]
        if eastmoney and "returned_items=0" not in eastmoney[-1].timing:
            assert "fallback source used" in eastmoney[-1].timing


def test_china_holding_change_source_contract(live_endpoint):
    end = date.today()
    with live_endpoint(
        "cn.holding-changes.600519",
        source="Eastmoney disclosures -> CNINFO fallback",
    ) as audit:
        result = cn_sentiment.get_holding_changes("600519.SS", end.isoformat())
        records = extract_provenance(result)
        assert isinstance(result, str) and result.strip()
        assert records
        sources = " -> ".join(record.source for record in records)
        audit.observe(source=sources, last_observation=_latest_event_date(result))
        assert {
            "major-shareholder holding changes",
            "executive holding changes",
        } <= {record.evidence for record in records}
        cninfo = [record for record in records if record.source == "CNINFO"]
        if cninfo and "no qualifying records" not in cninfo[-1].timing:
            assert "fallback source used" in cninfo[-1].timing


def test_japan_10y_source_date_and_frequency_contract(live_endpoint):
    _start, end = _settled_window(days=180)
    requested_end = date.fromisoformat(end)
    with live_endpoint("jp.jp10y", source="MOF -> FRED") as audit:
        data = jp_macro.fetch_series("jp_10y_yield", end, look_back_days=180)
        assert data is not None
        latest, value = _assert_macro_points(data, requested_end)
        audit.observe(source=data["actual_source"], last_observation=latest)
        assert 0 < value < 20
        if data["actual_source"] == "Japan Ministry of Finance":
            assert data["frequency"] == "Daily"
            assert latest >= requested_end - timedelta(days=15)
            assert "09:30 JST" in data["timing"]
            assert "fallback_reason" not in data
        else:
            assert data["actual_source"] == "FRED"
            assert data["frequency"] == "Monthly"
            assert latest >= requested_end - timedelta(days=75)
            assert data["fallback_reason"]


@pytest.mark.parametrize(
    ("indicator", "lower", "upper", "frequency", "max_age_days"),
    (
        ("cn_cpi", -100, 100, "Monthly", 75),
        ("cn_gdp", -100, 100, "Quarterly", 150),
        ("cn_pmi", 0, 100, "Monthly", 75),
    ),
)
def test_china_recent_macro_source_contract(
    indicator, lower, upper, frequency, max_age_days, live_endpoint
):
    end = date.today()
    with live_endpoint(
        f"cn.macro.{indicator}", source="NBS -> Eastmoney"
    ) as audit:
        data = cn_macro.fetch_series(indicator, end.isoformat(), look_back_days=180)
        assert data is not None
        latest, value = _assert_macro_points(data, end)
        audit.observe(source=data["actual_source"], last_observation=latest)
        assert lower <= value <= upper
        assert data["frequency"] == frequency
        assert latest >= end - timedelta(days=max_age_days)
        if data["actual_source"] == cn_macro._NBS_SOURCE:
            assert date.fromisoformat(data["release_date"]) <= end
            assert data["observation_period"]
            assert "official release-date filtered" in data["timing"]
            assert "fallback_reason" not in data
        else:
            assert data["actual_source"] == "Eastmoney"
            assert data["fallback_reason"]
            assert "non-vintage" in data["timing"]


def test_china_10y_source_shape_contract(live_endpoint):
    end = date.today()
    with live_endpoint(
        "cn.macro.cn10y", source="Eastmoney -> ChinaMoney"
    ) as audit:
        data = cn_macro.fetch_series("cn_10y_yield", end.isoformat(), look_back_days=45)

        assert data is not None
        latest, value = _assert_macro_points(data, end)
        audit.observe(source=data["actual_source"], last_observation=latest)
        assert latest >= end - timedelta(days=15)
        assert 0 < value < 20
        if data["actual_source"] == "Eastmoney":
            assert data["frequency"] == "Daily"
            assert len(data["points"]) > 1
            assert "fallback_reason" not in data
        else:
            assert data["actual_source"] == "China Foreign Exchange Trade System"
            assert data["frequency"] == "Latest official curve snapshot"
            assert len(data["points"]) == 1
            assert data["fallback_reason"]


def test_usd_cny_safe_primary_and_unit_contract(live_endpoint):
    end = date.today()
    with live_endpoint("cn.macro.usd-cny", source="SAFE -> Eastmoney") as audit:
        data = cn_macro.fetch_series("usd_cny", end.isoformat(), look_back_days=45)

        assert data is not None
        latest, value = _assert_macro_points(data, end)
        audit.observe(source=data["actual_source"], last_observation=latest)
        assert data["actual_source"] == "SAFE"
        assert data["frequency"] == "Daily"
        assert "fallback_reason" not in data
        assert latest >= end - timedelta(days=15)
        assert 5 < value < 10
