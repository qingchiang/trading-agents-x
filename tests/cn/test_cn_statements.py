"""Disclosure-date and entity-mapping tests for China financial statements."""

from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows.cn import cn_statements, sina_finance
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.provenance import extract_provenance


def _frame(*, bank: bool = False) -> pd.DataFrame:
    data = {
        "ReportDate": pd.to_datetime(["2025-12-31", "2025-09-30"]),
        "PublishDate": pd.to_datetime(["2026-03-20", "2025-10-25"]),
        "UpdateDate": pd.to_datetime(["2026-03-21", "2025-10-26"]),
        "VisibilityDate": pd.to_datetime(["2026-03-21", "2025-10-26"]),
        "Currency": ["CNY", "CNY"],
        "Audited": ["yes", "no"],
        "营业收入": [1000, 700],
        "净利润": [100, 70],
        "资产总计": [5000, 4800],
        "负债合计": [3000, 2900],
        "经营活动产生的现金流量净额": [200, 120],
    }
    if bank:
        data.update(
            {
                "利息净收入": [500, 350],
                "吸收存款": [3500, 3400],
                "发放贷款及垫款": [3200, 3100],
                "客户存款和同业存放款项净增加额": [80, 60],
            }
        )
    return pd.DataFrame(data)


def test_cashflow_preserves_sina_outflows_with_alternate_labels(monkeypatch):
    frame = _frame()
    frame["购建固定资产、无形资产和其他长期资产所支付的现金"] = [125, 80]
    frame["分配股利、利润或偿付利息所支付的现金"] = [45, 30]
    monkeypatch.setattr(cn_statements, "get_company_profile", lambda _: pd.DataFrame())
    monkeypatch.setattr(cn_statements, "get_statement_frame", lambda *_: None)
    monkeypatch.setattr(cn_statements, "fetch_finance_records", lambda *_: ("600309.SS", frame))
    output = cn_statements.get_cashflow("600309.SS", curr_date="2026-03-21")
    assert "Missing mapped fields: Capital expenditure" not in output
    assert ",125," in output
    assert ",45," in output


@pytest.mark.unit
def test_visibility_uses_later_conflicting_date_and_keeps_old_revision():
    frame = pd.DataFrame(
        {
            "ReportDate": ["2025-12-31", "2025-12-31", "2025-09-30"],
            "PublishDate": ["2026-03-20", "2026-03-20", "2025-10-20"],
            "UpdateDate": ["2026-07-01", "2026-03-21", "2025-10-21"],
            "VisibilityDate": ["2026-07-01", "2026-03-21", "2025-10-21"],
            "营业收入": [110, 100, 70],
        }
    )

    visible = sina_finance.filter_visible_records(frame, "2026-04-01")

    assert visible["ReportDate"].dt.strftime("%Y-%m-%d").tolist() == [
        "2025-12-31",
        "2025-09-30",
    ]
    assert visible.iloc[0]["营业收入"] == 100
    assert visible.iloc[0]["VisibilityDate"] == pd.Timestamp("2026-03-21")


@pytest.mark.unit
def test_annual_filter_and_future_disclosure_exclusion():
    frame = _frame()
    visible = sina_finance.filter_visible_records(frame, "2026-03-20", "annual")
    assert visible.empty  # update on 2026-03-21 is the conservative visible date

    visible = sina_finance.filter_visible_records(frame, "2026-03-21", "annual")
    assert visible["ReportDate"].tolist() == [pd.Timestamp("2025-12-31")]


@pytest.mark.unit
def test_missing_optional_disclosure_dates_remain_filterable():
    frame = _frame()
    frame.loc[0, "PublishDate"] = pd.NaT
    frame.loc[0, "UpdateDate"] = pd.NaT
    frame.loc[0, "VisibilityDate"] = frame.loc[0, "ReportDate"]

    visible = sina_finance.filter_visible_records(frame, "2026-01-01")

    assert visible.iloc[0]["ReportDate"] == pd.Timestamp("2025-12-31")
    assert pd.isna(visible.iloc[0]["PublishDate"])


@pytest.mark.unit
def test_epoch_update_time_is_normalized_in_shanghai_timezone():
    # 2026-03-21 00:00:00 Asia/Shanghai is still 2026-03-20 in UTC.
    value = 1774022400
    assert sina_finance._date_value(value) == pd.Timestamp("2026-03-21")
    assert sina_finance._date_value(str(value)) == pd.Timestamp("2026-03-21")
    assert sina_finance._date_value("20260321") == pd.Timestamp("2026-03-21")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ticker", "bank", "expected"),
    [
        ("600519.SS", False, "Entity mapping: general"),
        ("000333.SZ", False, "Entity mapping: general"),
        ("000001.SZ", True, "Entity mapping: financial"),
    ],
)
def test_statement_mapping_covers_consumer_manufacturer_and_bank(
    monkeypatch, ticker, bank, expected
):
    frame = _frame(bank=bank)
    monkeypatch.setattr(
        cn_statements,
        "fetch_finance_records",
        lambda _ticker, _kind: (ticker, frame),
    )
    profile = pd.DataFrame({"所属行业": ["银行" if bank else "制造业"]})
    monkeypatch.setattr(cn_statements, "get_company_profile", lambda _ticker: profile)
    monkeypatch.setattr(cn_statements, "get_statement_frame", lambda *_args: None)

    output = cn_statements.get_income_statement(ticker, curr_date="2026-04-01")

    assert expected in output
    assert "2025-12-31" in output
    if bank:
        assert "Net interest income" in output
        assert "Gross profit" not in output
    else:
        assert "Revenue" in output
        assert "Net interest income" not in output
    sources = {record.source for record in extract_provenance(output)}
    assert "AkShare / Sina CompanyFinanceService" in sources


@pytest.mark.unit
def test_irrelevant_financial_template_columns_do_not_misclassify_manufacturer(
    monkeypatch,
):
    frame = _frame()
    frame["手续费及佣金净收入"] = pd.NA
    monkeypatch.setattr(
        cn_statements,
        "fetch_finance_records",
        lambda *_args: ("000333.SZ", frame),
    )
    monkeypatch.setattr(
        cn_statements,
        "get_company_profile",
        lambda *_args: pd.DataFrame({"所属行业": ["电气机械和器材制造业"]}),
    )
    monkeypatch.setattr(cn_statements, "get_statement_frame", lambda *_args: None)

    output = cn_statements.get_income_statement("000333.SZ", curr_date="2026-04-01")

    assert "Entity mapping: general" in output
    assert "Net fee and commission income" not in output


@pytest.mark.unit
def test_missing_sina_fields_use_labeled_non_strict_yfinance_supplement(monkeypatch):
    frame = _frame()
    monkeypatch.setattr(
        cn_statements,
        "fetch_finance_records",
        lambda *_args: ("600519.SS", frame),
    )
    monkeypatch.setattr(cn_statements, "get_company_profile", lambda *_args: pd.DataFrame())
    yf = pd.DataFrame(
        {pd.Timestamp("2025-12-31"): [1000.0, 100.0]},
        index=["Total Revenue", "Net Income"],
    )
    get_yf = mock.Mock(return_value=yf)
    monkeypatch.setattr(cn_statements, "get_statement_frame", get_yf)

    output = cn_statements.get_income_statement("600519.SS", curr_date="2026-04-01")

    assert "Supplemental line items (yfinance)" in output
    assert "Non-strict PIT" in output
    assert "may contain later revisions" in output
    get_yf.assert_called_once_with("600519.SS", "income", "quarterly", "2026-04-01")
    assert {r.source for r in extract_provenance(output)} == {
        "AkShare / Sina CompanyFinanceService",
        "yfinance statement supplement",
    }


@pytest.mark.unit
def test_all_null_sina_column_triggers_yfinance_supplement(monkeypatch):
    frame = _frame()
    frame["营业成本"] = pd.NA
    monkeypatch.setattr(
        cn_statements,
        "fetch_finance_records",
        lambda *_args: ("600519.SS", frame),
    )
    monkeypatch.setattr(
        cn_statements,
        "get_company_profile",
        lambda *_args: pd.DataFrame({"所属行业": ["制造业"]}),
    )
    get_yf = mock.Mock(
        return_value=pd.DataFrame(
            {pd.Timestamp("2025-12-31"): [800.0]}, index=["Gross Profit"]
        )
    )
    monkeypatch.setattr(cn_statements, "get_statement_frame", get_yf)

    output = cn_statements.get_income_statement("600519.SS", curr_date="2026-04-01")

    assert "Operating cost" in output
    assert "Operating cost" in output.split("# Missing mapped fields:", 1)[1].splitlines()[0]
    assert "Supplemental line items (yfinance)" in output
    get_yf.assert_called_once()


@pytest.mark.unit
def test_empty_yfinance_values_still_record_supplement_provenance(monkeypatch):
    frame = _frame()
    frame["营业成本"] = pd.NA
    monkeypatch.setattr(
        cn_statements,
        "fetch_finance_records",
        lambda *_args: ("600519.SS", frame),
    )
    monkeypatch.setattr(
        cn_statements,
        "get_company_profile",
        lambda *_args: pd.DataFrame({"所属行业": ["制造业"]}),
    )
    monkeypatch.setattr(
        cn_statements,
        "get_statement_frame",
        lambda *_args: pd.DataFrame(
            {pd.Timestamp("2025-12-31"): [pd.NA]}, index=["Gross Profit"]
        ),
    )

    output = cn_statements.get_income_statement("600519.SS", curr_date="2026-04-01")

    records = extract_provenance(output)
    supplement = next(r for r in records if r.source == "yfinance statement supplement")
    assert supplement.timing == "available; curated line items contained no values"


@pytest.mark.unit
def test_general_cashflow_keeps_supplier_and_employee_payments_separate():
    frame = _frame()
    frame["购买商品、接受劳务支付的现金"] = [300, 200]
    frame["支付给职工以及为职工支付的现金"] = [150, 100]

    table, _missing = cn_statements._render_sina_table(frame, "cashflow", "general")

    assert "Cash paid to suppliers" in table
    assert "Cash paid to employees" in table
    assert "Cash paid to suppliers/employees" not in table


@pytest.mark.unit
def test_no_disclosure_visible_raises_typed_no_data(monkeypatch):
    monkeypatch.setattr(
        cn_statements,
        "fetch_finance_records",
        lambda *_args: ("600519.SS", _frame()),
    )
    with pytest.raises(NoMarketDataError, match="no balance reports visible") as exc_info:
        cn_statements.get_balance_sheet("600519.SS", curr_date="2025-01-01")
    assert len(exc_info.value.availability_notes) == 1
    assert "AkShare / Sina Balance Sheet unavailable" in exc_info.value.availability_notes[0]


@pytest.mark.unit
def test_sina_statement_failure_is_preserved_for_router_fallback(monkeypatch):
    monkeypatch.setattr(
        cn_statements,
        "fetch_finance_records",
        mock.Mock(side_effect=sina_finance.AkShareSchemaError("changed columns")),
    )

    with pytest.raises(NoMarketDataError, match="primary source unavailable") as exc_info:
        cn_statements.get_income_statement("600519.SS", curr_date="2026-04-01")

    assert len(exc_info.value.availability_notes) == 1
    assert "AkShare / Sina Income Statement unavailable" in exc_info.value.availability_notes[0]
    assert "changed columns" in exc_info.value.availability_notes[0]


@pytest.mark.unit
def test_statement_rejects_invalid_date_before_vendor_request(monkeypatch):
    fetch = mock.Mock(side_effect=AssertionError("vendor was queried"))
    monkeypatch.setattr(cn_statements, "fetch_finance_records", fetch)

    with pytest.raises(ValueError, match="expected YYYY-MM-DD"):
        cn_statements.get_balance_sheet("600519.SS", curr_date="not-a-date")
    fetch.assert_not_called()


@pytest.mark.unit
def test_changed_normalized_schema_is_rejected():
    with pytest.raises(sina_finance.AkShareSchemaError, match="VisibilityDate"):
        sina_finance.filter_visible_records(
            pd.DataFrame({"ReportDate": ["2025-12-31"]}), "2026-04-01"
        )


@pytest.mark.unit
def test_sina_transport_parses_numeric_update_timestamp_and_timeout(monkeypatch):
    payload = {
        "result": {
            "data": {
                "report_date": [{"date_value": "2025-12-31"}],
                "report_list": {
                    "2025-12-31": {
                        "publish_date": "2026-03-20",
                        "update_time": 1774137600,
                        "data_source": "annual report",
                        "is_audit": "yes",
                        "rCurrency": "CNY",
                        "rType": "consolidated",
                        "data": [{"item_title": "营业收入", "item_value": "123"}],
                    }
                },
            }
        }
    }
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    get = mock.Mock(return_value=response)
    monkeypatch.setattr(sina_finance, "load_akshare", lambda: object())
    monkeypatch.setattr(sina_finance.requests, "get", get)

    _canonical, frame = sina_finance.fetch_finance_records("600519.SS", "income")

    assert frame.iloc[0]["营业收入"] == 123
    assert frame.iloc[0]["VisibilityDate"] >= pd.Timestamp("2026-03-20")
    assert get.call_args.kwargs["timeout"] == sina_finance.REQUEST_TIMEOUT
