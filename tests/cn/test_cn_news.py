"""A-share news source, quality, history, and fault-isolation tests."""

from datetime import UTC, datetime
from unittest import mock

import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.cn import cn_news, google_news, news_sources
from tradingagents.dataflows.cn.common import AkShareSchemaError
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.news_quality import (
    build_chinese_company_aliases,
    classify_chinese_google_article,
)
from tradingagents.provenance import extract_provenance, provenance_quality_issues


@pytest.fixture(autouse=True)
def clear_cninfo_directory_cache():
    news_sources._cninfo_org_ids.cache_clear()
    news_sources._clear_feed_cache()
    yield
    clear = getattr(news_sources._cninfo_org_ids, "cache_clear", None)
    if clear:
        clear()
    news_sources._clear_feed_cache()


@pytest.mark.unit
def test_cninfo_empty_window_is_normal_empty_not_key_error(monkeypatch):
    responses = iter(
        [
            {"stockList": [{"code": "600519", "orgId": "gssh0600519"}]},
            {"totalAnnouncement": 0, "announcements": None},
        ]
    )
    monkeypatch.setattr(news_sources, "_request_json", lambda *_args, **_kwargs: next(responses))

    assert news_sources.disclosure_rows("600519.SS", "2026-01-01", "2026-01-10") == []


@pytest.mark.unit
def test_cninfo_missing_announcements_field_is_schema_failure(monkeypatch):
    responses = iter(
        [
            {"stockList": [{"code": "600519", "orgId": "gssh0600519"}]},
            {"error": "blocked"},
        ]
    )
    monkeypatch.setattr(
        news_sources, "_request_json", lambda *_args, **_kwargs: next(responses)
    )

    with pytest.raises(AkShareSchemaError, match="announcements"):
        news_sources.disclosure_rows("600519.SS", "2026-01-01", "2026-01-10")


@pytest.mark.unit
def test_cninfo_rechecks_exact_code_and_shanghai_date(monkeypatch):
    monkeypatch.setattr(news_sources, "_cninfo_org_ids", lambda: {"600519": "org"})
    in_window = int(datetime(2026, 1, 10, 15, tzinfo=UTC).timestamp() * 1000)
    future = int(datetime(2026, 1, 11, 16, tzinfo=UTC).timestamp() * 1000)
    monkeypatch.setattr(
        news_sources,
        "_request_json",
        lambda *_args, **_kwargs: {
            "announcements": [
                {
                    "secCode": "600519",
                    "secName": "贵州茅台",
                    "announcementTitle": "<em>业绩</em>公告",
                    "announcementTime": in_window,
                    "announcementId": "1",
                    "orgId": "org",
                },
                {
                    "secCode": "600518",
                    "announcementTitle": "wrong entity",
                    "announcementTime": in_window,
                    "announcementId": "2",
                },
                {
                    "secCode": "600519",
                    "announcementTitle": "future",
                    "announcementTime": future,
                    "announcementId": "3",
                },
            ]
        },
    )

    rows = news_sources.disclosure_rows("600519.SS", "2026-01-01", "2026-01-10")

    assert [row["title"] for row in rows] == ["业绩公告"]


@pytest.mark.unit
def test_research_rows_drop_future_and_other_stock(monkeypatch):
    monkeypatch.setattr(
        news_sources,
        "_request_json",
        lambda *_args, **_kwargs: {
            "data": [
                {
                    "stockCode": "000001",
                    "publishDate": "2026-01-10",
                    "title": "银行研报",
                    "infoCode": "ok",
                    "emRatingName": "买入",
                    "lastEmRatingName": "中性",
                },
                {"stockCode": "000002", "publishDate": "2026-01-10", "title": "wrong"},
                {"stockCode": "000001", "publishDate": "2026-01-11", "title": "future"},
            ]
        },
    )

    rows = news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-10")

    assert [row["title"] for row in rows] == ["银行研报"]
    assert rows[0]["rating_change"] == "中性 -> 买入"


@pytest.mark.unit
def test_research_missing_data_field_is_schema_failure(monkeypatch):
    monkeypatch.setattr(
        news_sources, "_request_json", lambda *_args, **_kwargs: {"message": "error"}
    )

    with pytest.raises(AkShareSchemaError, match="data"):
        news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-10")


@pytest.mark.unit
def test_cninfo_short_and_wider_windows_share_one_same_cutoff_superset(monkeypatch):
    monkeypatch.setattr(news_sources, "_cninfo_org_ids", lambda: {"600519": "org"})
    calls = []

    def request(*_args, **kwargs):
        calls.append(kwargs["data"])
        return {
            "announcements": [
                {
                    "secCode": "600519",
                    "announcementTitle": "older announcement",
                    "announcementTime": int(
                        datetime(2025, 12, 25, tzinfo=UTC).timestamp() * 1000
                    ),
                    "announcementId": "old",
                    "orgId": "org",
                },
                {
                    "secCode": "600519",
                    "announcementTitle": "recent announcement",
                    "announcementTime": int(
                        datetime(2026, 1, 5, tzinfo=UTC).timestamp() * 1000
                    ),
                    "announcementId": "new",
                    "orgId": "org",
                },
            ]
        }

    monkeypatch.setattr(news_sources, "_request_json", request)

    short = news_sources.disclosure_rows("600519.SS", "2026-01-01", "2026-01-10")
    wider = news_sources.disclosure_rows("600519.SS", "2025-12-20", "2026-01-10")

    assert [row["title"] for row in short] == ["recent announcement"]
    assert {row["title"] for row in wider} == {
        "older announcement",
        "recent announcement",
    }
    assert len(calls) == 1
    assert calls[0]["seDate"] == "2025-10-13~2026-01-10"


@pytest.mark.unit
def test_research_short_and_wider_windows_share_one_same_cutoff_superset(monkeypatch):
    calls = []

    def request(*_args, **kwargs):
        calls.append(kwargs["params"])
        return {
            "data": [
                {
                    "stockCode": "000001",
                    "publishDate": "2025-12-25",
                    "title": "older report",
                },
                {
                    "stockCode": "000001",
                    "publishDate": "2026-01-05",
                    "title": "recent report",
                },
            ]
        }

    monkeypatch.setattr(news_sources, "_request_json", request)

    short = news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-10")
    wider = news_sources.research_rows("000001.SZ", "2025-12-20", "2026-01-10")

    assert [row["title"] for row in short] == ["recent report"]
    assert {row["title"] for row in wider} == {"older report", "recent report"}
    assert len(calls) == 1
    assert calls[0]["beginTime"] == "2025-10-13"


@pytest.mark.unit
def test_low_frequency_cache_never_reuses_a_later_cutoff(monkeypatch):
    calls = []

    def request(*_args, **kwargs):
        calls.append(kwargs["params"]["endTime"])
        return {"data": []}

    monkeypatch.setattr(news_sources, "_request_json", request)

    news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-10")
    news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-09")

    assert calls == ["2026-01-10", "2026-01-09"]


@pytest.mark.unit
def test_low_frequency_cache_expires_and_does_not_cache_schema_failures(monkeypatch):
    clock = [0.0]
    calls = []

    def request(*_args, **_kwargs):
        calls.append(clock[0])
        if len(calls) == 1:
            return {"message": "invalid response"}
        return {"data": []}

    monkeypatch.setattr(news_sources.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(news_sources, "_request_json", request)

    with pytest.raises(AkShareSchemaError):
        news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-10")
    news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-10")
    news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-10")
    clock[0] = news_sources._FEED_CACHE_TTL_SECONDS + 1
    news_sources.research_rows("000001.SZ", "2026-01-01", "2026-01-10")

    assert calls == [0.0, 0.0, news_sources._FEED_CACHE_TTL_SECONDS + 1]


@pytest.mark.unit
def test_chinese_quality_never_promotes_code_or_short_name_to_direct():
    aliases = build_chinese_company_aliases("600519", "贵州茅台酒股份有限公司", "贵州茅台")

    assert classify_chinese_google_article("600519 今日行情", "未知", aliases).tier == "drop"
    assert (
        classify_chinese_google_article("贵州茅台发布业绩", "证券时报", aliases).tier == "candidate"
    )
    assert (
        classify_chinese_google_article("贵州茅台酒股份有限公司发布业绩", "证券时报", aliases).tier
        == "direct"
    )


@pytest.mark.unit
def test_google_news_deduplicates_and_drops_future(monkeypatch):
    monkeypatch.setattr(
        google_news, "_company_names", lambda _ticker: ("贵州茅台酒股份有限公司", "贵州茅台")
    )
    monkeypatch.setattr(
        google_news,
        "_fetch_items",
        lambda _query: [
            {
                "title": "贵州茅台酒股份有限公司发布业绩",
                "source": "证券时报",
                "published": datetime(2026, 1, 10, 10),
            },
            {
                "title": "贵州茅台酒股份有限公司：发布业绩",
                "source": "证券时报",
                "published": datetime(2026, 1, 10, 9),
            },
            {
                "title": "贵州茅台酒股份有限公司未来事项",
                "source": "证券时报",
                "published": datetime(2026, 1, 11, 1),
            },
        ],
    )

    result = google_news.get_news("600519.SS", "2026-01-01", "2026-01-10")

    assert result.count("### [direct]") == 1
    assert "未来事项" not in result


@pytest.mark.unit
def test_google_news_queries_short_and_legal_names(monkeypatch):
    queries = []
    monkeypatch.setattr(
        google_news,
        "_company_names",
        lambda _ticker: ("贵州茅台酒股份有限公司", "贵州茅台"),
    )
    monkeypatch.setattr(
        google_news,
        "_fetch_items",
        lambda query: queries.append(query) or [],
    )

    google_news.get_news("600519.SS", "2026-01-01", "2026-01-10")

    assert set(queries) == {
        '"贵州茅台" 600519 股票',
        '"贵州茅台酒股份有限公司" 600519 股票',
    }


@pytest.mark.unit
def test_google_news_keeps_successful_name_query_when_other_fails(monkeypatch):
    monkeypatch.setattr(
        google_news,
        "_company_names",
        lambda _ticker: ("贵州茅台酒股份有限公司", "贵州茅台"),
    )

    def fetch(query):
        if "股份有限公司" in query:
            raise TimeoutError("legal-name query timed out")
        return [
            {
                "title": "贵州茅台发布业绩",
                "source": "证券时报",
                "published": datetime(2026, 1, 10, 10),
            }
        ]

    monkeypatch.setattr(google_news, "_fetch_items", fetch)

    result = google_news.get_news("600519.SS", "2026-01-01", "2026-01-10")

    assert "贵州茅台发布业绩" in result
    assert "1 of 2 Google News name queries failed" in result


@pytest.mark.unit
def test_small_total_limit_keeps_every_cn_source_eligible(monkeypatch):
    monkeypatch.setattr(news_sources, "get_config", lambda: {"news_article_limit": 1})

    assert news_sources.news_quotas() == (1, 1, 1)


@pytest.mark.unit
def test_cn_assembler_preserves_other_sources_when_one_fails(monkeypatch):
    monkeypatch.setattr(cn_news, "_disclosure_news", lambda *_args: "## DISCLOSURES\n\n### item")
    monkeypatch.setattr(cn_news, "_research_news", mock.Mock(side_effect=TimeoutError))
    monkeypatch.setattr(cn_news, "_google_news", lambda *_args: "No media")

    result = cn_news.get_news("600519.SS", "2026-01-01", "2026-01-10")

    assert "DISCLOSURES" in result
    assert "<Eastmoney Research unavailable: TimeoutError>" in result
    assert '"source":"CNINFO"' in result


@pytest.mark.unit
@pytest.mark.parametrize(
    "google_output",
    [
        (
            "## MEDIA\n\n### [direct] item\nbody\n\n"
            "Query availability note: 1 of 2 Google News name queries failed."
        ),
        (
            "<Google News China partially unavailable: 1 of 2 name queries failed; "
            "successful queries returned no relevant items>"
        ),
    ],
)
def test_cn_assembler_promotes_partial_google_query_failure_to_provenance_warning(
    monkeypatch, google_output
):
    monkeypatch.setattr(
        cn_news,
        "_disclosure_news",
        lambda *_args: "## DISCLOSURES\n\n### [direct] official item",
    )
    monkeypatch.setattr(cn_news, "_research_news", lambda *_args: "No research")
    monkeypatch.setattr(cn_news, "_google_news", lambda *_args: google_output)

    result = cn_news.get_news("600519.SS", "2026-01-01", "2026-01-10")
    records = extract_provenance(result)
    google_record = next(record for record in records if record.source == "Google News China")
    issues = provenance_quality_issues(records)

    assert google_record.timing.endswith("partial coverage; query_failures=1/2")
    assert any(
        issue.evidence == "get_news"
        and issue.source == "Google News China"
        and issue.reason == "partial coverage"
        for issue in issues
    )


@pytest.mark.unit
def test_cn_partial_empty_status_survives_yfinance_router_fallback(monkeypatch):
    monkeypatch.setattr(cn_news, "_disclosure_news", lambda *_args: "No disclosures")
    monkeypatch.setattr(cn_news, "_research_news", lambda *_args: "No research")
    monkeypatch.setattr(
        cn_news,
        "_google_news",
        lambda *_args: (
            "<Google News China partially unavailable: 1 of 2 name queries failed; "
            "successful queries returned no relevant items>"
        ),
    )
    fallback = mock.Mock(return_value="## FALLBACK\n\n### fallback item")

    with mock.patch.dict(
        interface.VENDOR_METHODS,
        {"get_news": {"cn_news": cn_news.get_news, "yfinance": fallback}},
    ), mock.patch.object(interface, "get_vendor", return_value="cn_news,yfinance"):
        result = interface.route_to_vendor(
            "get_news",
            "600519.SS",
            "2026-01-01",
            "2026-01-10",
            _provenance=True,
        )

    records = extract_provenance(result)
    issues = provenance_quality_issues(records)

    assert any(
        record.source == "Google News China"
        and record.timing == "partial coverage; query_failures=1/2"
        for record in records
    )
    assert any(
        record.source == "yfinance"
        and record.timing.startswith("fallback vendor selected;")
        for record in records
    )
    assert any(
        issue.source == "Google News China" and issue.reason == "partial coverage"
        for issue in issues
    )
    assert any(
        issue.source == "yfinance" and issue.reason == "fallback source used"
        for issue in issues
    )


@pytest.mark.unit
def test_cn_assembler_deduplicates_normalized_titles_across_sources(monkeypatch):
    monkeypatch.setattr(
        cn_news,
        "_disclosure_news",
        lambda *_args: "## DISCLOSURES\n\n### [direct] 公司发布业绩\nOfficial",
    )
    monkeypatch.setattr(
        cn_news,
        "_research_news",
        lambda *_args: "## RESEARCH\n\n### [direct] 公司：发布业绩 (institution: Broker)\nReport",
    )
    monkeypatch.setattr(cn_news, "_google_news", lambda *_args: "No media")

    result = cn_news.get_news("600519.SS", "2026-01-01", "2026-01-10")

    assert result.count("### [direct]") == 1
    assert "## RESEARCH" not in result


@pytest.mark.unit
def test_cn_assembler_raises_for_router_fallback_only_when_all_empty(monkeypatch):
    monkeypatch.setattr(cn_news, "_disclosure_news", lambda *_args: "No disclosures")
    monkeypatch.setattr(cn_news, "_research_news", lambda *_args: "No research")
    monkeypatch.setattr(cn_news, "_google_news", lambda *_args: "No media")

    with pytest.raises(NoMarketDataError):
        cn_news.get_news("600519.SS", "2026-01-01", "2026-01-10")


@pytest.mark.unit
def test_cn_assembler_enforces_total_limit_after_source_collection(monkeypatch):
    monkeypatch.setattr(cn_news, "get_config", lambda: {"news_article_limit": 1})
    monkeypatch.setattr(
        cn_news, "_disclosure_news", lambda *_args: "## A\n\n### [direct] A item"
    )
    monkeypatch.setattr(
        cn_news, "_research_news", lambda *_args: "## B\n\n### [direct] B item"
    )
    monkeypatch.setattr(
        cn_news, "_google_news", lambda *_args: "## C\n\n### [candidate] C item"
    )

    result = cn_news.get_news("600519.SS", "2026-01-01", "2026-01-10")

    assert result.count("### [") == 1
