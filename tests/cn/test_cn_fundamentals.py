"""China fundamentals assembler tests."""

from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows.cn import cn_fundamentals, common, company
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.rate_limit import stop_on_rate_limit_scope
from tradingagents.provenance import extract_evidence_spans, extract_provenance

_PROFILE_RETRIEVED_AT = "2026-07-19T02:03:04+00:00"


def _abstract(*, bank: bool = False) -> pd.DataFrame:
    data = {
        "ReportDate": pd.to_datetime(["2025-12-31"]),
        "PublishDate": pd.to_datetime(["2026-03-20"]),
        "UpdateDate": pd.to_datetime(["2026-03-21"]),
        "VisibilityDate": pd.to_datetime(["2026-03-21"]),
        "基本每股收益": [2.0],
        "营业总收入": [1000],
        "归属净利润": [100],
        "净资产收益率": [12.0],
    }
    if bank:
        data.update({"净息差": [2.1], "不良贷款率": [1.0], "资本充足率": [14.0]})
    return pd.DataFrame(data)


def _install_sources(monkeypatch, *, bank=False):
    profile = pd.DataFrame(
        {
            "公司名称": ["平安银行" if bank else "测试公司"],
            "A股简称": ["平安银行" if bank else "测试股份"],
            "所属行业": ["银行" if bank else "制造业"],
            "主营业务": ["存贷款" if bank else "生产销售"],
        }
    )
    monkeypatch.setattr(
        cn_fundamentals,
        "get_company_profile_snapshot",
        lambda *_args: company.CompanyProfileSnapshot(
            frame=profile,
            retrieved_at=_PROFILE_RETRIEVED_AT,
        ),
    )
    monkeypatch.setattr(
        cn_fundamentals,
        "fetch_finance_records",
        lambda ticker, _kind: (ticker, _abstract(bank=bank)),
    )


@pytest.mark.unit
def test_historical_analysis_never_queries_current_yfinance(monkeypatch):
    _install_sources(monkeypatch)
    current = mock.Mock(side_effect=AssertionError("current snapshot queried"))
    profile = mock.Mock(side_effect=AssertionError("current profile queried"))
    monkeypatch.setattr(cn_fundamentals, "get_yfinance_fundamentals", current)
    monkeypatch.setattr(cn_fundamentals, "get_company_profile_snapshot", profile)

    output = cn_fundamentals.get_fundamentals("600519.SS", "2026-04-01")

    current.assert_not_called()
    profile.assert_not_called()
    assert "Not requested: current-only valuation and forecasts are excluded" in output
    assert "Financial abstract" in output
    assert "CNINFO profile source status: unavailable" in output
    assert {record.source for record in extract_provenance(output)} == {
        "AkShare / CNINFO company profile",
        "AkShare / Sina financial abstract",
        "yfinance current valuation snapshot",
    }


@pytest.mark.unit
def test_live_analysis_adds_separate_yfinance_valuation_provenance(monkeypatch):
    _install_sources(monkeypatch)
    monkeypatch.setattr(
        cn_fundamentals,
        "is_near_live",
        lambda _date, _ticker: True,
    )
    get_yf = mock.Mock(return_value="Market Cap: 123\nPE Ratio (TTM): 10")
    monkeypatch.setattr(cn_fundamentals, "get_yfinance_fundamentals", get_yf)

    output = cn_fundamentals.get_fundamentals("600519.SS", "2026-07-19")

    get_yf.assert_called_once_with("600519.SS", "2026-07-19")
    assert "Current valuation and analyst snapshot (yfinance)" in output
    assert "Market Cap: 123" in output
    records = extract_provenance(output)
    assert len(records) == 3
    assert len({record.source for record in records}) == 3
    profile_record = next(
        record
        for record in records
        if record.source == "AkShare / CNINFO company profile"
    )
    assert profile_record.retrieved_at == _PROFILE_RETRIEVED_AT
    spans = extract_evidence_spans(output)
    assert {span.temporal_scope for span in spans} == {
        "point_in_time",
        "live_only",
    }
    assert next(
        span.content
        for span in spans
        if span.temporal_scope == "live_only"
        and span.content
        and "Market Cap: 123" in span.content
    ).endswith("PE Ratio (TTM): 10")
    assert "Effective period: 2025-12-31" in output


@pytest.mark.unit
def test_bank_uses_financial_metric_mapping(monkeypatch):
    _install_sources(monkeypatch, bank=True)
    monkeypatch.setattr(cn_fundamentals, "get_yfinance_fundamentals", lambda *_args: "")

    output = cn_fundamentals.get_fundamentals("000001.SZ")

    assert "Entity mapping: financial" in output
    assert "Net interest margin" in output
    assert "Non-performing loan ratio" in output
    assert "Gross margin" not in output


@pytest.mark.unit
def test_metric_matching_never_substitutes_growth_rate_for_amount():
    column = cn_fundamentals._find_column(
        ["营业总收入同比增长率"], ("营业总收入", "营业收入")
    )
    assert column is None


@pytest.mark.unit
def test_profile_failure_still_returns_disclosure_abstract(monkeypatch):
    monkeypatch.setattr(
        cn_fundamentals,
        "get_company_profile_snapshot",
        mock.Mock(side_effect=RuntimeError("profile down")),
    )
    monkeypatch.setattr(
        cn_fundamentals,
        "fetch_finance_records",
        lambda ticker, _kind: (ticker, _abstract()),
    )
    monkeypatch.setattr(cn_fundamentals, "get_yfinance_fundamentals", lambda *_args: "")

    output = cn_fundamentals.get_fundamentals("000333.SZ")

    assert "CNINFO profile source status: unavailable" in output
    assert "2025-12-31" in output
    assert {r.source for r in extract_provenance(output)} == {
        "AkShare / CNINFO company profile",
        "AkShare / Sina financial abstract",
        "yfinance current valuation snapshot",
    }


@pytest.mark.unit
def test_both_china_sources_unavailable_raises_for_router_fallback(monkeypatch):
    monkeypatch.setattr(
        cn_fundamentals,
        "get_company_profile_snapshot",
        lambda *_args: company.CompanyProfileSnapshot(
            frame=pd.DataFrame(),
            retrieved_at=_PROFILE_RETRIEVED_AT,
        ),
    )
    monkeypatch.setattr(
        cn_fundamentals,
        "fetch_finance_records",
        mock.Mock(side_effect=RuntimeError("abstract down")),
    )
    get_yf = mock.Mock()
    monkeypatch.setattr(cn_fundamentals, "get_yfinance_fundamentals", get_yf)

    with pytest.raises(NoMarketDataError) as exc_info:
        cn_fundamentals.get_fundamentals("600519.SS", "2026-04-01")
    assert len(exc_info.value.availability_notes) == 2
    assert "CNINFO company profile unavailable" in exc_info.value.availability_notes[0]
    assert "Sina financial abstract unavailable" in exc_info.value.availability_notes[1]
    get_yf.assert_not_called()


@pytest.mark.unit
def test_incremental_fundamentals_stop_before_later_sources_on_rate_limit(
    monkeypatch,
):
    later = mock.Mock(side_effect=AssertionError("later source queried after 429"))
    monkeypatch.setattr(cn_fundamentals, "is_near_live", lambda *_args: True)
    monkeypatch.setattr(
        cn_fundamentals,
        "get_company_profile_snapshot",
        mock.Mock(side_effect=common.AkShareRateLimitError("CNINFO 429")),
    )
    monkeypatch.setattr(cn_fundamentals, "fetch_finance_records", later)

    with (
        stop_on_rate_limit_scope(True),
        pytest.raises(common.AkShareRateLimitError, match="CNINFO 429"),
    ):
        cn_fundamentals.get_fundamentals("600519.SS", "2026-07-24")

    later.assert_not_called()


@pytest.mark.unit
def test_invalid_date_is_rejected_before_any_fundamental_source(monkeypatch):
    profile = mock.Mock(side_effect=AssertionError("profile was queried"))
    abstract = mock.Mock(side_effect=AssertionError("abstract was queried"))
    monkeypatch.setattr(cn_fundamentals, "get_company_profile_snapshot", profile)
    monkeypatch.setattr(cn_fundamentals, "fetch_finance_records", abstract)

    with pytest.raises(ValueError, match="expected YYYY-MM-DD"):
        cn_fundamentals.get_fundamentals("600519.SS", "2026/04/01")
    profile.assert_not_called()
    abstract.assert_not_called()


@pytest.mark.unit
def test_cninfo_profile_transport_caches_snapshot_and_returns_defensive_copy(
    monkeypatch,
):
    company.clear_cache()
    values = {
        key: value
        for value, (key, _label) in enumerate(company._PROFILE_FIELD_MAP)
    }
    values = dict(reversed(list(values.items())))
    values["NEW_TRAILING_FIELD"] = "ignored"
    response = mock.Mock()
    response.json.return_value = {"count": 1, "records": [values]}
    response.raise_for_status.return_value = None
    post = mock.Mock(return_value=response)
    monkeypatch.setattr(company, "_cninfo_headers", lambda: {"Accept-Enckey": "signed"})
    monkeypatch.setattr(company.requests, "post", post)

    first = company.get_company_profile_snapshot("600519.SS")
    first.frame.iloc[0, 0] = 999
    second = company.get_company_profile_snapshot("600519.SS")
    compatible_frame = company.get_company_profile("600519.SS")

    assert second.frame.iloc[0]["公司名称"] == 0
    assert second.frame.shape == (1, 26)
    assert compatible_frame.iloc[0]["公司名称"] == 0
    assert first.retrieved_at == second.retrieved_at
    assert second.retrieved_at.endswith("+00:00")
    assert post.call_count == 1
    assert post.call_args.kwargs["timeout"] == company.REQUEST_TIMEOUT
    assert post.call_args.kwargs["params"] == {"scode": "600519"}
    company.clear_cache()


@pytest.mark.unit
def test_cninfo_changed_record_shape_is_typed_schema_error(monkeypatch):
    company.clear_cache()
    response = mock.Mock()
    response.json.return_value = {"count": 1, "records": [{"ORGNAME": "only one"}]}
    response.raise_for_status.return_value = None
    monkeypatch.setattr(company, "_cninfo_headers", lambda: {})
    monkeypatch.setattr(company.requests, "post", lambda *_args, **_kwargs: response)

    with pytest.raises(company.AkShareSchemaError, match="missing required key"):
        company.get_company_profile("000333.SZ")
    company.clear_cache()
