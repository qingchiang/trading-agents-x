from datetime import UTC, datetime, timedelta


def test_news_cache_reuses_refresh_and_retains_disappeared_candidates(tmp_path):
    from tradingagents.dataflows.config import get_config, use_config
    from tradingagents.dataflows.news_cache import fetch_news_feed

    current = datetime(2026, 9, 5, 10, tzinfo=UTC)
    calls = []

    def fetch():
        calls.append(1)
        return ("## feed\n\n### old event\nPublished: 2026-09-03T10:00:00Z"
                if len(calls) == 1 else "No news found")

    with use_config({**get_config(), "data_cache_dir": str(tmp_path)}):
        first = fetch_news_feed("test", "GOOG", "2026-09-01", "2026-09-05", fetch, now=lambda: current)
        second = fetch_news_feed("test", "GOOG", "2026-09-01", "2026-09-05", fetch, now=lambda: current + timedelta(minutes=1))
        third = fetch_news_feed("test", "GOOG", "2026-09-01", "2026-09-05", fetch, now=lambda: current + timedelta(minutes=16))
    assert len(calls) == 2
    assert all("old event" in body for body in (first, second, third))
    assert "2026-09-05T10:00:00+00:00" in third


def test_cache_refresh_scope_revision_and_failure(tmp_path):
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.news_cache import fetch_news_feed
    from tradingagents.dataflows.news_selection import emit_news, split_candidates
    from tradingagents.dataflows.source_observations import capture_observations

    current = datetime(2026, 9, 5, 10, tzinfo=UTC)
    config = {**get_config(), "data_cache_dir": str(tmp_path)}
    calls = []

    def fetch(text="original"):
        calls.append(1)
        return f"## feed\n\n### event\nPublished: 2026-09-03\n{text}\nLink: https://example.com/1"

    def run(start="2026-09-01", budget=100, delta=0, producer=fetch, cfg=None):
        return fetch_news_feed("one", "GOOG", start, "2026-09-05", producer,
                               budget=budget, now=lambda: current + timedelta(minutes=delta), config=cfg or config)

    run(start="2026-09-03", budget=10)
    run()  # A narrow, small response cannot certify the wider request.
    assert len(calls) == 2
    revised = run(delta=16, producer=lambda: fetch("corrected"))
    row = split_candidates(revised)[1][0]
    assert row.revision and "corrected" in row.content
    with capture_observations() as observations:
        emit_news(revised, "one", "GOOG")
    assert not observations[0].is_pit
    assert observations[0].retrieved_at == current + timedelta(minutes=16)

    def fail():
        raise RuntimeError("no transport")

    stale = run(delta=32, producer=fail)
    assert "refresh failed: RuntimeError" in stale and "corrected" in stale
    # Another source/configuration cannot read this source's saved material.
    other = fetch_news_feed("two", "GOOG", "2026-09-01", "2026-09-05",
                            lambda: "No news found", config=config)
    assert "event" not in other
    excluded = run(cfg={**config, "tool_vendors": {"get_news": "different"}}, producer=lambda: "No news found")
    assert "event" not in excluded


def test_cache_eviction_corruption_and_disabled_mode(tmp_path):
    import sqlite3

    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.news_cache import fetch_news_feed
    from tradingagents.dataflows.news_selection import split_candidates

    config = {**get_config(), "data_cache_dir": str(tmp_path), "news_cache_scope_limit": 2,
              "news_cache_total_limit": 3}
    current = datetime(2026, 9, 5, tzinfo=UTC)
    body = "## feed" + "".join(f"\n\n### event{i}\nPublished: 2026-09-0{i+1}" for i in range(4))
    for source in ("one", "two"):
        fetch_news_feed(source, "GOOG", "2026-09-01", "2026-09-05", lambda: body, config=config, now=lambda: current)
    path = tmp_path / "news/sources.sqlite3"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM articles").fetchone()[0] == 3
        assert max(n for n, in connection.execute("SELECT count(*) FROM articles GROUP BY scope")) <= 2
    late = fetch_news_feed("one", "GOOG", "2026-09-01", "2026-12-10", lambda: "No news found",
                           config=config, now=lambda: current + timedelta(days=96))
    assert not split_candidates(late)[1]
    path.write_bytes(b"broken database")
    calls = []
    def fetch():
        calls.append(1)
        return body
    assert fetch_news_feed("one", "GOOG", "2026-09-01", "2026-09-05", fetch, config=config) == body
    assert len(calls) == 1
    assert fetch_news_feed("one", "GOOG", "2026-09-01", "2026-09-05", fetch,
                           config={**config, "news_cache_enabled": False}) == body
    assert len(calls) == 2


def test_cache_concurrent_writes_remain_readable(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.news_cache import fetch_news_feed

    config = {**get_config(), "data_cache_dir": str(tmp_path)}
    def request(index):
        return fetch_news_feed("one", "GOOG", "2026-09-01", "2026-09-05",
                               lambda: f"## feed\n\n### event{index}\nPublished: 2026-09-03",
                               budget=index + 1, config=config)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request, range(4)))
    assert all("event" in result for result in results)
    merged = fetch_news_feed("one", "GOOG", "2026-09-01", "2026-09-05", lambda: "No news found", config=config)
    assert all(f"event{i}" in merged for i in range(4))


def test_cached_material_enters_full_and_incremental_with_original_time(tmp_path):
    from langchain_core.messages import ToolMessage

    from tests.dataflows.test_incremental_us_collector import _request
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.incremental_us import _collect_news
    from tradingagents.dataflows.news_cache import fetch_news_feed
    from tradingagents.dataflows.news_selection import finalize_news
    from tradingagents.graph.research_graph import _collect_evidence

    current = datetime(2026, 9, 5, 10, tzinfo=UTC)
    config = {**get_config(), "data_cache_dir": str(tmp_path)}
    args = ("yfinance", "GOOG", "2026-09-01", "2026-09-05")
    fetch_news_feed(*args, lambda: "## news\n\n### event\nPublished: 2026-09-03T10:00:00Z", config=config, now=lambda: current)
    def route(*_args, **_kwargs):
        block = fetch_news_feed(*args, lambda: "No news found", config=config, now=lambda: current + timedelta(minutes=16))
        return finalize_news(block, "yfinance", "GOOG", args[2], args[3], 30)
    body = route()
    full = _collect_evidence([ToolMessage(content=body, tool_call_id="news", name="get_news")], "",
                             requested_date=current.date(), analyst="news")
    request = _request(enabled_domains=("news",), baseline=current.date()-timedelta(days=4),
                       target=current.date(), window_start=current-timedelta(days=4), window_end=current)
    _, incremental = _collect_news(request, route_to_vendor=route, now=lambda: current)
    assert len(full) == len(incremental) == 1
    assert full[0].provenance["observation_identity"] == incremental[0].evidence.provenance["observation_identity"]
    assert full[0].origins[0].retrieved_at == current.isoformat()


def test_cache_preserves_market_local_publication_across_utc_date_boundary(tmp_path):
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.news_cache import fetch_news_feed
    from tradingagents.dataflows.news_selection import split_candidates

    config = {**get_config(), "data_cache_dir": str(tmp_path)}
    body = fetch_news_feed("JP", "9984.T", "2026-09-04", "2026-09-04",
                           lambda: "## feed\n\n### event\nPublished: 2026-09-03T16:00:00Z", config=config)
    row = split_candidates(body)[1][0]
    assert row.day.isoformat() == "2026-09-04"


def test_global_cache_uses_utc_publication_day(tmp_path):
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.news_cache import fetch_news_feed
    from tradingagents.dataflows.news_selection import split_candidates

    config = {**get_config(), "data_cache_dir": str(tmp_path)}
    current = datetime(2026, 9, 5, 10, tzinfo=UTC)
    calls = []

    def fetch():
        calls.append(1)
        return "## feed\n\n### event\nPublished: 2026-09-05T01:00:00Z"

    # Seed the same source/scope using the previous market-local calendar policy.
    fetch_news_feed(
        "global-source",
        "global",
        "2026-09-05",
        "2026-09-05",
        fetch,
        config=config,
        now=lambda: current,
    )
    body = fetch_news_feed(
        "global-source",
        "global",
        "2026-09-05",
        "2026-09-05",
        fetch,
        config=config,
        now=lambda: current,
        global_feed=True,
    )

    assert len(calls) == 2
    row = split_candidates(body)[1][0]
    assert row.day.isoformat() == "2026-09-05"


def test_refresh_failure_survives_incremental_admission_without_changing_article_identity(tmp_path, monkeypatch):
    from tests.dataflows.test_incremental_us_collector import _request
    from tradingagents.application.incremental_collection import normalize_incremental_collection
    from tradingagents.dataflows import incremental_inputs
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.incremental_us import collect_us_incremental
    from tradingagents.dataflows.news_cache import fetch_news_feed
    from tradingagents.dataflows.news_selection import finalize_news

    current = datetime(2026, 9, 5, 10, tzinfo=UTC)
    config = {**get_config(), "data_cache_dir": str(tmp_path)}
    request = _request(enabled_domains=("news",), baseline=current.date()-timedelta(days=4),
                       target=current.date(), window_start=current-timedelta(days=4), window_end=current)
    monkeypatch.setattr(incremental_inputs, "get_global_macro_panel", lambda *_: "")
    attempt_time = current
    def fetch():
        if attempt_time == current:
            return "## news\n\n### event\nPublished: 2026-09-03T10:00:00Z"
        raise RuntimeError("source unavailable")
    def route(method, *_args, **_kwargs):
        if method != "get_news":
            return "No news found"
        block = fetch_news_feed("yfinance", "GOOG", "2026-09-01", "2026-09-05", fetch,
                                config=config, now=lambda: attempt_time)
        return finalize_news(block, "yfinance", "GOOG", "2026-09-01", "2026-09-05", 30)
    original = collect_us_incremental(request, route_to_vendor=route, now=lambda: current)
    attempt_time = current + timedelta(minutes=16)
    refreshed = collect_us_incremental(request, route_to_vendor=route, now=lambda: attempt_time)
    summary, items, _ = normalize_incremental_collection(request, refreshed, sealed_at=attempt_time)
    assert len(items) == 1
    assert "cache refresh failed" in items[0].origins[0].timing
    assert items[0].origins[0].retrieved_at == current.isoformat()
    assert summary.domains[0].sources[0].diagnostic.code == "news_cache_refresh_failed"
    assert items[0].provenance["observation_identity"] == original.evidence[0].evidence.provenance["observation_identity"]


def test_cache_tracks_content_reversion_as_a_new_observed_version(tmp_path):
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.news_cache import fetch_news_feed
    from tradingagents.dataflows.news_selection import split_candidates

    config = {**get_config(), "data_cache_dir": str(tmp_path), "news_cache_refresh_seconds": 0}
    current = datetime(2026, 9, 3, 10, tzinfo=UTC)
    def request(text, now, end="2026-09-05"):
        return fetch_news_feed("source", "GOOG", "2026-09-01", end,
                               lambda: f"## feed\n\n### event\nPublished: 2026-09-01\n{text}\nLink: https://example.test/1",
                               config=config, now=lambda: now)
    first = request("A", current)
    request("B", current + timedelta(days=1))
    reverted = request("A", current + timedelta(days=2))
    row = split_candidates(reverted)[1][0]
    assert "\nA\n" in row.content
    assert row.retrieved_at == (current + timedelta(days=2)).isoformat()
    assert row.revision
    # Original history remains accessible; reading it does not backdate A's reversion.
    past = fetch_news_feed("source", "GOOG", "2026-09-01", "2026-09-03", lambda: "No news found",
                           config=config, now=lambda: current + timedelta(days=2, minutes=1))
    assert split_candidates(past)[1][0].retrieved_at == split_candidates(first)[1][0].retrieved_at


def test_full_news_separates_availability_diagnostics_from_articles():
    from langchain_core.messages import ToolMessage

    from tradingagents.dataflows.news_selection import NewsCandidate, render_candidate
    from tradingagents.graph.research_graph import _collect_evidence
    from tradingagents.provenance import ProvenanceRecord, attach_evidence_span, attach_provenance

    current = datetime(2026, 9, 5, 10, tzinfo=UTC)
    article = NewsCandidate("cninfo", "real event", "### real event", "2026-09-04T10:00:00+08:00",
                            retrieved_at=current.isoformat())
    success = ProvenanceRecord("get_news", "cninfo", "2026-09-01 to 2026-09-05", "2026-09-04", "publication-date filtered")
    failure = ProvenanceRecord("get_news", "eastmoney", "2026-09-01 to 2026-09-05", "unknown", "source unavailable")
    body = attach_evidence_span(attach_provenance("## feed\n\n" + render_candidate(article), success), temporal_scope="point_in_time")
    body += "\n\n" + attach_provenance("### Source availability notes\n<Eastmoney unavailable>", failure)
    evidence = _collect_evidence([ToolMessage(content=body, name="get_news", tool_call_id="news")], "",
                                 requested_date=current.date(), analyst="news", instrument="600309.SS")
    articles = [item for item in evidence if item.evidence_type == "news_article"]
    assert len(articles) == 1
    assert articles[0].source == "cninfo"
    diagnostics = [item for item in evidence if item.content is None]
    assert any(origin.source == "eastmoney" and "unavailable" in origin.timing for item in diagnostics for origin in item.origins)


def test_cn_query_recovery_does_not_revise_unchanged_cached_article(tmp_path, monkeypatch):
    import sqlite3

    from tradingagents.dataflows.cn import google_news
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.news_cache import fetch_news_feed
    from tradingagents.dataflows.news_selection import split_candidates

    current = datetime(2026, 9, 5, 10, tzinfo=UTC)
    config = {**get_config(), "data_cache_dir": str(tmp_path)}
    partial = True
    monkeypatch.setattr(google_news, "_company_names", lambda _: ("贵州茅台酒股份有限公司", "贵州茅台"))
    def fetch(query):
        if "股份有限公司" in query:
            if partial:
                raise TimeoutError("unavailable")
            return []
        return [{"title": "贵州茅台发布业绩", "source": "证券时报", "published": datetime(2026, 9, 4, 10)}]
    monkeypatch.setattr(google_news, "_fetch_items", fetch)
    args = ("google", "600519.SS", "2026-09-01", "2026-09-05", lambda: google_news.get_news("600519.SS", "2026-09-01", "2026-09-05"))
    first = fetch_news_feed(*args, config=config, now=lambda: current)
    partial = False
    recovered = fetch_news_feed(*args, config=config, now=lambda: current + timedelta(minutes=16))
    first_header, first_rows = split_candidates(first)
    recovered_header, recovered_rows = split_candidates(recovered)
    assert "name queries failed" in first_header
    assert "name queries failed" not in recovered_header
    assert first_rows == recovered_rows
    assert not recovered_rows[0].revision
    assert recovered_rows[0].retrieved_at == current.isoformat()
    with sqlite3.connect(tmp_path / "news" / "sources.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
