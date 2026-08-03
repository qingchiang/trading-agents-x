"""A-share sentiment rendering and coverage-state tests."""

from datetime import date

import pandas as pd
import pytest

from tradingagents.dataflows.cn import cn_sentiment
from tradingagents.dataflows.cn.common import AkShareRequestError, AkShareSchemaError
from tradingagents.provenance import (
    append_provenance_appendix,
    extract_provenance,
    strip_provenance_markers,
)


@pytest.fixture(autouse=True)
def clear_holding_cache():
    cn_sentiment._clear_holding_cache()
    yield
    cn_sentiment._clear_holding_cache()


@pytest.mark.unit
def test_research_signal_is_publication_date_bounded(monkeypatch):
    monkeypatch.setattr(cn_sentiment, "sina_rating_rows", lambda *_args: [])
    monkeypatch.setattr(
        cn_sentiment,
        "research_rows",
        lambda *_args: [
            {
                "published": date(2026, 1, 10),
                "institution": "Broker",
                "rating": "Buy",
                "rating_change": "upgrade",
                "target_low": 10,
                "target_high": 12,
                "title": "Report",
            }
        ],
    )

    result = cn_sentiment.get_research_signal("000001.SZ", "2026-01-10")

    assert "2026-01-10" in result
    assert "upgrade" in result
    _body, facts = cn_sentiment.get_research_signal_payload(
        "000001.SZ", "2026-01-10"
    )
    assert [(fact["key"], fact["value"], fact["unit"]) for fact in facts] == [
        ("target_low_1", 10.0, "CNY"),
        ("target_high_1", 12.0, "CNY"),
    ]
    assert {fact["effective_date"] for fact in facts} == {"2026-01-10"}


@pytest.mark.unit
def test_research_signal_prefers_sina_without_querying_eastmoney(monkeypatch):
    monkeypatch.setattr(
        cn_sentiment,
        "sina_rating_rows",
        lambda *_args: [
            {
                "published": date(2026, 1, 10),
                "institution": "Sina Broker",
                "analyst": "Analyst A",
                "rating": "买入",
                "rating_change": "reiterated 买入",
                "target_low": 10,
                "target_high": 12,
            }
        ],
    )
    monkeypatch.setattr(
        cn_sentiment,
        "research_rows",
        lambda *_args: (_ for _ in ()).throw(AssertionError("fallback queried")),
    )

    result = cn_sentiment.get_research_signal("000001.SZ", "2026-01-10")

    assert "Sina Finance; publication-date filtered" in result
    assert "analysts=Analyst A" in result
    assert [record.source for record in extract_provenance(result)] == [
        "Sina Finance institutional ratings"
    ]


@pytest.mark.unit
def test_research_signal_uses_eastmoney_after_sina_failure(monkeypatch):
    monkeypatch.setattr(
        cn_sentiment,
        "sina_rating_rows",
        lambda *_args: (_ for _ in ()).throw(AkShareRequestError("Sina failed")),
    )
    monkeypatch.setattr(
        cn_sentiment,
        "research_rows",
        lambda *_args: [
            {
                "published": date(2026, 1, 10),
                "institution": "Eastmoney Broker",
                "rating": "Buy",
                "rating_change": "upgrade",
                "target_low": 10,
                "target_high": 12,
                "title": "Report",
            }
        ],
    )

    result = cn_sentiment.get_research_signal("000001.SZ", "2026-01-10")
    records = extract_provenance(result)

    assert "Eastmoney Research; publication-date filtered" in result
    assert "Sina rating feed unavailable" in result
    assert [record.source for record in records] == [
        "Sina Finance institutional ratings",
        "Eastmoney Research",
    ]
    assert "fallback source used" in records[1].timing


@pytest.mark.unit
def test_research_successful_empty_primary_and_fallback_do_not_warn(monkeypatch):
    monkeypatch.setattr(cn_sentiment, "sina_rating_rows", lambda *_args: [])
    monkeypatch.setattr(cn_sentiment, "research_rows", lambda *_args: [])

    result = cn_sentiment.get_research_signal("000001.SZ", "2026-01-10")
    report = append_provenance_appendix(
        "REPORT", extract_provenance(result), enabled=False
    )

    assert "no usable coverage" in result
    assert "Data Quality Warnings" not in report


@pytest.mark.unit
def test_holding_changes_distinguishes_no_events(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"result": {"data": []}}

    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())

    result = cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")

    assert strip_provenance_markers(result).startswith(
        "<Eastmoney holding changes: no matching events"
    )


@pytest.mark.unit
def test_holding_changes_handles_same_date_and_converts_ten_thousand_shares(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {
                "result": {
                    "data": [
                        {
                            "NOTICE_DATE": "2026-01-10",
                            "SECURITY_CODE": "600519",
                            "HOLDER_NAME": "Holder A",
                            "DIRECTION": "增持",
                            "CHANGE_NUM": 2.5,
                        },
                        {
                            "NOTICE_DATE": "2026-01-10",
                            "SECURITY_CODE": "600519",
                            "HOLDER_NAME": "Holder B",
                            "DIRECTION": "减持",
                            "CHANGE_NUM": 1,
                        },
                    ]
                }
            }

    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())

    result = cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")

    assert "shares=25,000" in result
    assert "shares=10,000" in result
    assert "timing=disclosure/update-date filtered" in result


@pytest.mark.unit
def test_holding_changes_labels_event_date_only_major_holder_data_as_non_strict_pit(
    monkeypatch,
):
    class Response:
        def __init__(self, report_name):
            self.report_name = report_name

        def json(self):
            if self.report_name == "RPT_SHARE_HOLDER_INCREASE":
                return {
                    "result": {
                        "data": [
                            {
                                "END_DATE": "2026-01-09",
                                "SECURITY_CODE": "600519",
                                "HOLDER_NAME": "Holder A",
                                "DIRECTION": "增持",
                                "CHANGE_NUM": 1,
                            }
                        ]
                    }
                }
            return {"result": {"data": []}}

    monkeypatch.setattr(
        cn_sentiment,
        "_request",
        lambda *_args, **kwargs: Response(kwargs["params"]["reportName"]),
    )

    result = cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")

    assert "[major shareholder] Holder A" in result
    assert "timing=event-date only; non-strict PIT" in result
    assert "non-strict PIT" in extract_provenance(result)[0].timing


@pytest.mark.unit
def test_holding_changes_include_executive_transactions(monkeypatch):
    class Response:
        def __init__(self, report_name):
            self.report_name = report_name

        def json(self):
            if self.report_name == "RPT_EXECUTIVE_HOLD_DETAILS":
                return {
                    "result": {
                        "data": [
                            {
                                "CHANGE_DATE": "2026-01-09",
                                "SECURITY_CODE": "600519",
                                "EITIME": "2026-01-10",
                                "PERSON_NAME": "Executive A",
                                "POSITION_NAME": "Director",
                                "CHANGE_SHARES": -1200,
                            }
                        ]
                    }
                }
            return {"result": {"data": []}}

    monkeypatch.setattr(
        cn_sentiment,
        "_request",
        lambda *_args, **kwargs: Response(kwargs["params"]["reportName"]),
    )

    result = cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")

    assert "[executive] Executive A (Director); 减持; shares=1,200" in result
    assert "2026-01-10" in result
    assert "timing=disclosure/update-date filtered" in result


@pytest.mark.unit
def test_holding_changes_labels_event_date_only_executive_data_as_non_strict_pit(
    monkeypatch,
):
    class Response:
        def __init__(self, report_name):
            self.report_name = report_name

        def json(self):
            if self.report_name == "RPT_EXECUTIVE_HOLD_DETAILS":
                return {
                    "result": {
                        "data": [
                            {
                                "CHANGE_DATE": "2026-01-09",
                                "SECURITY_CODE": "600519",
                                "PERSON_NAME": "Executive A",
                                "POSITION_NAME": "Director",
                                "CHANGE_SHARES": 1200,
                            }
                        ]
                    }
                }
            return {"result": {"data": []}}

    monkeypatch.setattr(
        cn_sentiment,
        "_request",
        lambda *_args, **kwargs: Response(kwargs["params"]["reportName"]),
    )

    result = cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")

    assert "timing=event-date only; non-strict PIT" in result


@pytest.mark.unit
def test_holding_api_error_is_not_reported_as_empty(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"success": False, "message": "bad query", "result": None}

    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        cn_sentiment,
        "disclosure_rows",
        lambda *_args: (_ for _ in ()).throw(AkShareRequestError("CNINFO failed")),
    )

    with pytest.raises(AkShareRequestError, match="bad query"):
        cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")


@pytest.mark.unit
def test_holding_errors_fall_back_to_exact_code_cninfo_announcements(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"success": False, "message": "bad query", "result": None}

    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        cn_sentiment,
        "disclosure_rows",
        lambda *_args: [
            {
                "published": pd.Timestamp("2026-01-09", tz="Asia/Shanghai").to_pydatetime(),
                "title": "关于控股股东增持公司股份的公告",
            }
        ],
    )

    result = cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")
    records = extract_provenance(result)

    assert "[official announcement fallback] 关于控股股东增持公司股份的公告" in result
    assert records[-1].source == "CNINFO"
    assert "fallback source used" in records[-1].timing


@pytest.mark.unit
def test_holding_changes_uses_server_window_and_reports_single_page_truncation(
    monkeypatch,
):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    calls = []

    def request(*_args, **kwargs):
        params = kwargs["params"]
        calls.append(params)
        report_name = params["reportName"]
        if report_name == "RPT_EXECUTIVE_HOLD_DETAILS":
            return Response({"result": {"data": [], "pages": 1}})
        return Response(
            {
                "result": {
                    "data": [
                        {
                            "NOTICE_DATE": "2026-01-09",
                            "SECURITY_CODE": "600519",
                            "HOLDER_NAME": "Historical Holder",
                            "DIRECTION": "增持",
                            "CHANGE_NUM": 2,
                        }
                    ],
                    "pages": 2,
                }
            }
        )

    monkeypatch.setattr(cn_sentiment, "_request", request)

    result = cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")

    assert "Historical Holder" in result
    assert "latest 100 window records used; coverage is incomplete" in result
    assert len(calls) == 2
    assert all(params["pageNumber"] == 1 for params in calls)
    assert "NOTICE_DATE>='2025-10-13'" in calls[0]["filter"]
    assert "NOTICE_DATE<='2026-01-10'" in calls[0]["filter"]
    assert "CHANGE_DATE>='2025-10-13'" in calls[1]["filter"]


@pytest.mark.unit
def test_holding_changes_caches_same_window(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"result": {"data": [], "pages": 1}}

    calls = []

    def fetch(*_args, **kwargs):
        calls.append(kwargs["params"]["reportName"])
        return Response()

    monkeypatch.setattr(cn_sentiment, "_request", fetch)

    cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")
    cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")

    assert calls == ["RPT_SHARE_HOLDER_INCREASE", "RPT_EXECUTIVE_HOLD_DETAILS"]


@pytest.mark.unit
def test_holding_no_data_code_is_normal_empty(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"success": False, "code": 9201, "message": "返回数据为空", "result": None}

    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())

    result = cn_sentiment.get_holding_changes("600519.SS", "2026-01-10")

    assert strip_provenance_markers(result).startswith(
        "<Eastmoney holding changes: no matching events"
    )


@pytest.mark.unit
def test_sse_margin_preserves_legitimate_zero_values(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {
                "result": [
                    {
                        "stockCode": "600519",
                        "rzye": 0,
                        "rzmre": 0,
                        "rqyl": 0,
                    }
                ]
            }

    monkeypatch.setattr(
        cn_sentiment, "previous_trade_date", lambda _date: date(2026, 1, 9)
    )
    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())

    result = cn_sentiment.get_margin_signal("600519.SS", "2026-01-10")

    assert "financing balance=0 CNY" in result
    assert "financing buys=0 CNY" in result
    assert "securities-lending balance=0 shares" in result


@pytest.mark.unit
def test_sse_margin_rejects_unverifiable_single_row(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"result": [{"unknownCode": "600519", "rzye": 1}]}

    monkeypatch.setattr(
        cn_sentiment, "previous_trade_date", lambda _date: date(2026, 1, 9)
    )
    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())

    with pytest.raises(AkShareSchemaError, match="security-code"):
        cn_sentiment.get_margin_signal("600519.SS", "2026-01-10")


@pytest.mark.unit
def test_sse_margin_rejects_unknown_metric_fields_without_positional_guessing(
    monkeypatch,
):
    class Response:
        @staticmethod
        def json():
            return {
                "result": [
                    {
                        "stockCode": "600519",
                        **{f"opaque{index}": index for index in range(13)},
                    }
                ]
            }

    monkeypatch.setattr(
        cn_sentiment, "previous_trade_date", lambda _date: date(2026, 1, 9)
    )
    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())

    with pytest.raises(AkShareSchemaError, match="recognized financing fields"):
        cn_sentiment.get_margin_signal("600519.SS", "2026-01-10")


@pytest.mark.unit
def test_sz_margin_no_covered_row_is_not_neutral(monkeypatch):
    class Response:
        content = b"xlsx"

    monkeypatch.setattr(cn_sentiment, "previous_trade_date", lambda _date: date(2026, 1, 9))
    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        pd, "read_excel", lambda *_args, **_kwargs: pd.DataFrame({"证券代码": ["000002"]})
    )

    result = cn_sentiment.get_margin_signal(
        "000001.SZ", "2026-01-10", _remaining_sessions=1
    )

    assert result.startswith("<SZSE margin detail: no covered row for 000001")


@pytest.mark.unit
def test_sz_margin_rejects_matching_row_without_known_metric_columns(monkeypatch):
    class Response:
        content = b"xlsx"

    monkeypatch.setattr(cn_sentiment, "previous_trade_date", lambda _date: date(2026, 1, 9))
    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        pd,
        "read_excel",
        lambda *_args, **_kwargs: pd.DataFrame(
            {"证券代码": ["000001"], "unknown metric": [123]}
        ),
    )

    with pytest.raises(AkShareSchemaError, match="recognized financing columns"):
        cn_sentiment.get_margin_signal("000001.SZ", "2026-01-10")


@pytest.mark.unit
def test_sz_margin_walks_back_when_latest_workbook_not_published(monkeypatch):
    class Response:
        content = b"xlsx"

    dates = iter([date(2026, 1, 9), date(2026, 1, 8), date(2026, 1, 8)])
    monkeypatch.setattr(
        cn_sentiment, "previous_trade_date", lambda *_args, **_kwargs: next(dates)
    )
    monkeypatch.setattr(cn_sentiment, "_request", lambda *_args, **_kwargs: Response())
    frames = iter(
        [
            pd.DataFrame({"证券代码": []}),
            pd.DataFrame(
                {
                    "证券代码": ["000001"],
                    "融资余额(元)": ["5,000"],
                    "融资买入额(元)": ["800"],
                    "融券余量(股/份)": ["100"],
                }
            ),
        ]
    )
    monkeypatch.setattr(pd, "read_excel", lambda *_args, **_kwargs: next(frames))

    result = cn_sentiment.get_margin_signal("000001.SZ", "2026-01-10")

    assert "on 2026-01-08" in result
    assert "financing balance=5,000 CNY" in result
