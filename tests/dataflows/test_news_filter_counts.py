"""Source-side counts explain losses before the shared news selector runs."""

from datetime import UTC, datetime

import pytest


@pytest.mark.parametrize("source", ["disclosure", "research"])
def test_cn_source_counts_survive_shared_memory_cache(source, monkeypatch):
    from tradingagents.dataflows.cn import news_sources

    news_sources._clear_feed_cache()
    monkeypatch.setattr(news_sources, "_cninfo_org_ids", lambda: {"600309": "org"})
    calls = []
    def fetch(*_a, **_k):
        calls.append(1)
        if source == "disclosure":
            def row(day, index):
                return {"secCode": "600309", "announcementTitle": "Same event", "announcementId": str(index),
                        "announcementTime": int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)}
            return {"announcements": [row("2025-01-01", i) for i in range(98)] + [row("2026-09-03", i) for i in (98, 99)]}
        def row(day):
            return {"stockCode": "600309", "title": "Same event", "publishDate": day, "infoCode": "x"}
        return {"data": [row("2025-01-01") for _ in range(98)] + [row("2026-09-03") for _ in range(2)]}
    monkeypatch.setattr(news_sources, "_request_json", fetch)
    producer = news_sources.get_disclosure_news if source == "disclosure" else news_sources.get_research_news
    try:
        first = producer("600309.SS", "2026-09-01", "2026-09-05")
        hot = producer("600309.SS", "2026-09-02", "2026-09-05")
        for body in (first, hot):
            assert "upstream_returned=100" in body
            assert "date_filtered=98" in body
            assert "relevance_filtered=0" in body
            assert "duplicates=1" in body
            assert "candidates=1" in body
        assert len(calls) == 1
    finally:
        news_sources._clear_feed_cache()


def test_edinet_counts_cover_security_filter_and_source_cap(monkeypatch):
    from tradingagents.dataflows.config import get_config, use_config
    from tradingagents.dataflows.jp import edinet_news

    rows = [
        {"secCode": "99840", "docDescription": "one", "submitDateTime": "2026-09-03 10:00", "docID": "1"},
        {"secCode": "99840", "docDescription": "two", "submitDateTime": "2026-09-03 11:00", "docID": "2"},
        {"secCode": "72030", "docDescription": "other"},
    ]
    monkeypatch.setattr(edinet_news, "documents_on", lambda *_: rows)
    with use_config({**get_config(), "news_article_limit": 1}):
        body = edinet_news.get_news("9984.T", "2026-09-03", "2026-09-03")
    assert "upstream_returned=3" in body
    assert "relevance_filtered=1" in body
    assert "source_truncated=1" in body
    assert "candidates=1" in body


def test_tdnet_counts_cover_parse_date_security_and_cap(monkeypatch):
    from tests.jp.test_tdnet_news import _page, _row
    from tradingagents.dataflows.config import get_config, use_config
    from tradingagents.dataflows.jp import tdnet_news

    page = _page(_row(when="2026/09/03 10:00", title="one"),
                 _row(when="2026/09/03 11:00", title="two"),
                 _row(when="2026/09/02 10:00"),
                 _row(when="2026/09/03 10:00", code="99840"),
                 _row(when="invalid"))
    monkeypatch.setattr(tdnet_news, "_search", lambda *_: page)
    monkeypatch.setattr(tdnet_news, "tokyo_today", lambda: datetime(2026, 9, 5).date())
    with use_config({**get_config(), "news_article_limit": 1}):
        body = tdnet_news.get_news("7203.T", "2026-09-03", "2026-09-03")
    assert "upstream_returned=5" in body
    assert "date_filtered=2" in body
    assert "relevance_filtered=1" in body
    assert "source_truncated=1" in body
    assert "candidates=1" in body
