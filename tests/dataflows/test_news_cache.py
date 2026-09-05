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
