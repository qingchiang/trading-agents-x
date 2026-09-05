from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tests.application.test_service import _equity_resolver, _Graph, _service
from tradingagents.application.contracts import (
    AnalysisRequest,
    IncrementalCollectionRequest,
    RunStatus,
)
from tradingagents.application.incremental_collection import normalize_incremental_collection
from tradingagents.application.service import AnalysisService, default_incremental_synthesizer
from tradingagents.dataflows import incremental_us, interface, y_finance as yf_data
from tradingagents.dataflows.config import bind_config, reset_config
from tradingagents.dataflows.errors import VendorRateLimitError
from tradingagents.dataflows.incremental_us import collect_us_incremental
from tradingagents.dataflows.rate_limit import stop_on_rate_limit_requested
from tradingagents.provenance import ProvenanceRecord, attach_provenance


@pytest.fixture(autouse=True)
def _isolate_shared_background(monkeypatch):
    from tradingagents.dataflows import incremental_inputs

    monkeypatch.setattr(incremental_inputs, "get_global_macro_panel", lambda *_: "")
    monkeypatch.setattr(incremental_inputs, "get_market_investor_flows", lambda *_: "")


def _request(
    *,
    enabled_domains=("market",),
    baseline=date(2026, 7, 20),
    target=date(2026, 7, 24),
    window_start=datetime(2026, 7, 20, 23, 59, tzinfo=UTC),
    window_end=datetime(2026, 7, 24, 23, 59, tzinfo=UTC),
) -> IncrementalCollectionRequest:
    return IncrementalCollectionRequest(
        version="1",
        instrument="NVDA",
        market="united_states",
        route_suffix="",
        baseline_analysis_cutoff=baseline,
        analysis_cutoff=target,
        window_start=window_start,
        window_end=window_end,
        enabled_domains=enabled_domains,
        configured_routes={"data_vendors": {"core_stock_apis": "yfinance"}},
    )


def _market_response() -> str:
    body = """# Stock data for NVDA from 2026-07-01 to 2026-07-24
# Price adjustment: auto-adjusted prices (yfinance auto_adjust=True)
# Actual data source: yfinance

Date,Open,High,Low,Close,Volume
2026-07-17,99,101,98,100,1000
2026-07-20,100,102,99,101,1000
2026-07-24,109,111,108,110,1000
2026-07-25,111,112,110,111,1000
"""
    return attach_provenance(
        body,
        ProvenanceRecord(
            evidence="get_stock_data",
            source="yfinance",
            requested="2026-07-20 to 2026-07-24",
            effective="2026-07-19 to 2026-07-25",
            timing="market-date filtered",
            retrieved_at="2026-07-25T01:00:00Z",
        ),
    )


def test_social_observed_range_describes_messages_not_requested_window():
    result = collect_us_incremental(
        _request(enabled_domains=("social",)),
        fetch_stocktwits_messages=lambda *a, **kw: (
            "[2026-07-24 12:00:00 EDT · @one · Bullish] first\n"
            "[2026-07-24 13:00:00 EDT · @two · no-label] second"
        ),
        now=lambda: datetime(2026, 7, 25, tzinfo=UTC),
    )
    domain = result.collection_summary.domains[0]
    assert domain.observed_from == datetime(2026, 7, 24, 16, tzinfo=UTC)
    assert domain.observed_through == datetime(2026, 7, 24, 17, tzinfo=UTC)


def test_us_collector_reuses_routed_broader_adjusted_series_and_truncates_it() -> None:
    calls = []

    def route(method, *args, **kwargs):
        calls.append((method, args, kwargs))
        return _market_response()

    result = collect_us_incremental(
        _request(),
        route_to_vendor=route,
        fetch_stocktwits_messages=lambda *_args, **_kwargs: "unused",
        now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC),
    )

    assert result.stock_series is not None
    assert [point.session.isoformat() for point in result.stock_series.points] == [
        "2026-07-17",
        "2026-07-20",
        "2026-07-24",
    ]
    assert result.stock_series.source == "yfinance"
    assert result.stock_series.adjustment_basis == "yfinance_auto_adjusted_close"
    assert result.stock_series_evidence_ref == result.evidence[0].evidence.ref
    assert result.collection_summary.domains[0].sources[0].source == "yfinance"
    assert calls[0][:2] == ("get_stock_data", ("NVDA", "2026-07-13", "2026-07-24"))


def test_us_collector_uses_exact_instrument_identity_and_only_completed_nyse_sessions() -> None:
    body = """# Stock data for NVDAA from 2026-06-27 to 2026-07-07
# Price adjustment: auto-adjusted prices (yfinance auto_adjust=True)
# Actual data source: yfinance

Date,Open,High,Low,Close,Volume
2026-07-02,99,101,98,100,1000
2026-07-03,100,102,99,101,1000
2026-07-04,101,103,100,102,1000
2026-07-06,102,104,101,103,1000
2026-07-07,103,105,102,104,1000
"""
    response = attach_provenance(
        body,
        ProvenanceRecord(
            evidence="get_stock_data",
            source="yfinance",
            requested="2026-06-27 to 2026-07-07",
            effective="2026-07-02 to 2026-07-07",
            timing="market-date filtered",
            retrieved_at="2026-07-08T01:00:00Z",
        ),
    )
    mismatched = collect_us_incremental(
        _request(baseline=date(2026, 7, 4), target=date(2026, 7, 7)),
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 8, 2, tzinfo=UTC),
    )
    assert mismatched.collection_summary.domains[0].diagnostic.code == "market_instrument_mismatch"

    eligible_body = response.replace("NVDAA", "NVDA")
    collected = collect_us_incremental(
        _request(
            baseline=date(2026, 7, 4),
            target=date(2026, 7, 7),
            window_start=datetime(2026, 7, 4, 23, 59, tzinfo=UTC),
            window_end=datetime(2026, 7, 7, 23, 59, tzinfo=UTC),
        ),
        route_to_vendor=lambda *_args, **_kwargs: eligible_body,
        now=lambda: datetime(2026, 7, 8, 2, tzinfo=UTC),
    )
    assert [point.session for point in collected.stock_series.points] == [
        date(2026, 7, 2),
        date(2026, 7, 6),
        date(2026, 7, 7),
    ]


def test_us_collector_omits_same_day_bar_before_new_york_close() -> None:
    request = _request(
        baseline=date(2026, 7, 20),
        target=date(2026, 7, 24),
        window_end=datetime(2026, 7, 24, 19, tzinfo=UTC),  # 15:00 New York
    )
    collected = collect_us_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: _market_response(),
        now=lambda: datetime(2026, 7, 24, 19, tzinfo=UTC),
    )
    assert [point.session for point in collected.stock_series.points] == [
        date(2026, 7, 17),
        date(2026, 7, 20),
    ]
    assert collected.collection_summary.domains[0].state.value == "empty"


def test_us_collector_preserves_precise_yahoo_publication_time_and_omits_later_same_day_news() -> None:
    request = _request(
        enabled_domains=("news",),
        window_end=datetime(2026, 7, 24, 19, tzinfo=UTC),
    )
    response = attach_provenance(
        """### [direct] Before cutoff (source: Example)
Published: 2026-07-24T18:00:00Z
inside

### [direct] After cutoff (source: Example)
Published: 2026-07-24T20:00:00Z
outside
""",
        ProvenanceRecord(
            evidence="get_news", source="yfinance", requested="window",
            effective="window", timing="publication-date filtered",
            retrieved_at="2026-07-24T18:30:00Z",
        ),
    )
    collected = collect_us_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 18, 30, tzinfo=UTC),
    )
    _summary, evidence, _bindings = normalize_incremental_collection(
        request, collected, sealed_at=datetime(2026, 7, 24, 18, 31, tzinfo=UTC)
    )
    assert [item.content.split("\n", 1)[0] for item in evidence] == ["Before cutoff"]
    assert evidence[0].available_at == datetime(2026, 7, 24, 18, tzinfo=UTC)


def test_us_collector_reports_yahoo_error_as_unavailable_with_actual_source() -> None:
    response = attach_provenance(
        "Error fetching news for NVDA: upstream unavailable",
        ProvenanceRecord(
            evidence="get_news", source="yfinance", requested="window",
            effective="—", timing="retrieval unavailable",
            retrieved_at="2026-07-25T01:00:00Z",
        ),
    )
    collected = collect_us_incremental(
        _request(enabled_domains=("news",)),
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC),
    )
    domain = collected.collection_summary.domains[0]
    assert domain.state.value == "unavailable"
    assert domain.diagnostic.code == "news_retrieval_failed.news_context_partial"
    assert domain.sources[0].source == "yfinance"


def test_us_collector_stops_the_journey_on_rate_limit_before_news_or_benchmarks() -> None:
    calls = []

    def route(method, *_args, **_kwargs):
        calls.append(method)
        raise VendorRateLimitError("Yahoo Finance rate limited")

    with pytest.raises(VendorRateLimitError):
        collect_us_incremental(
            _request(enabled_domains=("market", "news")),
            route_to_vendor=route,
            now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC),
        )
    assert calls == ["get_stock_data"]


def test_us_collector_stops_after_a_focused_fundamentals_info_rate_limit() -> None:
    calls = []

    class RateLimitedTicker:
        @property
        def info(self):
            calls.append("info")
            from yfinance.exceptions import YFRateLimitError

            raise YFRateLimitError()

    def get_fundamentals(*args, **kwargs):
        calls.append("get_fundamentals")
        return yf_data.get_fundamentals(*args, **kwargs)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(yf_data, "is_near_live", lambda *_args: True)
        monkeypatch.setattr(yf_data.yf, "Ticker", lambda *_args: RateLimitedTicker())
        monkeypatch.setitem(
            interface.VENDOR_METHODS["get_fundamentals"], "yfinance", get_fundamentals
        )
        token = bind_config({"data_vendors": {"fundamental_data": "yfinance"}})
        try:
            with pytest.raises(VendorRateLimitError, match="Yahoo Finance rate limited"):
                collect_us_incremental(
                    _request(enabled_domains=("fundamentals", "news", "market")),
                    now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC),
                )
        finally:
            reset_config(token)

    assert calls == ["get_fundamentals", "info"]


def test_us_collector_stops_on_stocktwits_rate_limit_before_later_domains() -> None:
    with pytest.raises(VendorRateLimitError):
        collect_us_incremental(
            _request(enabled_domains=("social", "market")),
            route_to_vendor=lambda *_args, **_kwargs: _market_response(),
            fetch_stocktwits_messages=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                VendorRateLimitError("StockTwits rate limited")
            ),
            now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC),
        )


def test_us_collector_enters_the_bounded_stocktwits_rate_limit_scope(monkeypatch) -> None:
    def rate_limited(*_args, **_kwargs):
        assert stop_on_rate_limit_requested()
        raise VendorRateLimitError("StockTwits rate limited")

    monkeypatch.setattr(incremental_us, "DEFAULT_STOCKTWITS_FETCH", rate_limited)
    with pytest.raises(VendorRateLimitError):
        collect_us_incremental(
            _request(enabled_domains=("social", "market")),
            route_to_vendor=lambda *_args, **_kwargs: _market_response(),
            now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC),
        )


def test_us_collector_starts_social_query_after_the_baseline_market_date() -> None:
    calls = []
    result = collect_us_incremental(
        _request(enabled_domains=("social",)),
        route_to_vendor=lambda *_args, **_kwargs: "unused",
        fetch_stocktwits_messages=lambda *args, **kwargs: calls.append((args, kwargs)) or (
            "<no StockTwits messages found>"
        ),
        now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC),
    )

    assert result.collection_summary.domains[0].state.value == "empty"
    assert calls[0][1]["start_date"] == "2026-07-21"
    assert calls[0][1]["end_date"] == "2026-07-24"


def test_xnys_schedule_handles_regular_holidays_early_close_and_adhoc_closure() -> None:
    assert incremental_us._market_close_at(date(2026, 7, 2)) == datetime(
        2026, 7, 2, 20, tzinfo=UTC
    )
    assert not incremental_us._is_nyse_session(date(2026, 7, 3))
    assert not incremental_us._is_nyse_session(date(2026, 7, 4))
    assert incremental_us._market_close_at(date(2026, 11, 27)) == datetime(
        2026, 11, 27, 18, tzinfo=UTC
    )
    assert not incremental_us._is_nyse_session(date(2018, 12, 5))


def test_us_collector_admits_dated_yahoo_news_and_retains_selected_fallback() -> None:
    request = _request(enabled_domains=("news",))
    response = attach_provenance(
        """### [direct] NVIDIA announces a new product (source: Example)
Published: 2026-07-22
The announcement was observed in the bounded Yahoo feed.
""",
        ProvenanceRecord(
            evidence="get_news",
            source="yfinance",
            requested="2026-07-20 to 2026-07-24",
            effective="2026-07-20 to 2026-07-24",
            timing="fallback vendor selected; publication-date filtered",
            retrieved_at="2026-07-25T01:00:00Z",
        ),
    )
    collected = collect_us_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: response,
        fetch_stocktwits_messages=lambda *_args, **_kwargs: "unused",
        now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC),
    )
    summary, evidence, _bindings = normalize_incremental_collection(
        request,
        collected,
        sealed_at=datetime(2026, 7, 25, 2, tzinfo=UTC),
    )

    assert summary.domains[0].state.value == "partial"
    assert summary.domains[0].sources[0].fallback is True
    assert evidence[0].effective_date == date(2026, 7, 22)
    assert evidence[0].available_at is not None


def test_us_collector_describes_an_empty_stocktwits_sample_without_historical_absence() -> None:
    result = collect_us_incremental(
        _request(enabled_domains=("social",)),
        route_to_vendor=lambda *_args, **_kwargs: "unused",
        fetch_stocktwits_messages=lambda *_args, **_kwargs: "<no StockTwits messages found>",
        now=lambda: datetime(2026, 7, 29, 15, tzinfo=UTC),
    )

    social = result.collection_summary.domains[0]
    assert social.state.value == "empty"
    assert social.diagnostic is not None
    assert social.diagnostic.code == "bounded_feed_no_observed_records"


def test_us_collector_omits_six_day_old_live_snapshot_at_shared_boundary() -> None:
    response = attach_provenance(
        "# Company Fundamentals for NVDA (live yfinance snapshot)\nMarket Cap: 1",
        ProvenanceRecord(
            evidence="get_fundamentals",
            source="yfinance",
            requested="2026-07-24",
            effective="data available for cutoff 2026-07-24",
            timing="live non-point-in-time",
            retrieved_at="2026-07-30T15:00:00Z",
        ),
    )
    request = _request(enabled_domains=("fundamentals",))

    collected = collect_us_incremental(
        request,
        route_to_vendor=lambda method, *_args, **_kwargs: response,
        fetch_stocktwits_messages=lambda *_args, **_kwargs: "unused",
        now=lambda: datetime(2026, 7, 30, 15, tzinfo=UTC),
    )
    summary, evidence, _bindings = normalize_incremental_collection(
        request,
        collected,
        sealed_at=datetime(2026, 7, 30, 15, 1, tzinfo=UTC),
    )

    assert evidence == ()
    assert summary.domains[0].state.value == "empty"
    assert summary.domains[0].omitted_by_temporal_boundary is True


def test_us_collector_admits_current_stocktwits_only_as_near_live_advisory() -> None:
    request = _request(enabled_domains=("social",))
    collected = collect_us_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: "unused",
        fetch_stocktwits_messages=lambda *_args, **_kwargs: "Bullish: 1 (100%)\n\n[message]",
        now=lambda: datetime(2026, 7, 29, 15, tzinfo=UTC),
    )
    summary, evidence, _bindings = normalize_incremental_collection(
        request,
        collected,
        sealed_at=datetime(2026, 7, 29, 15, 1, tzinfo=UTC),
    )

    assert summary.domains[0].temporal_bases == ("near_live_advisory",)
    assert evidence[0].available_at is None
    assert evidence[0].origins[0].temporal_scope == "live_only"


def test_default_us_collector_commits_a_full_to_incremental_service_journey(
    monkeypatch,
    app_settings,
    repository,
) -> None:
    monkeypatch.setattr(
        incremental_us,
        "DEFAULT_ROUTE_TO_VENDOR",
        lambda method, *_args, **_kwargs: _market_response(),
    )
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20), analysts=("market",))
    )
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_synthesizer=default_incremental_synthesizer,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            analysts=("market",),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    node = next(node for node in repository.get_timeline("NVDA").all_nodes if node.id == result.run_id)
    assert node.performance.stock.status.value == "calculated"
    assert node.information_advancement.reasons == (
        "admissible_observation",
        "completed_stock_session",
    )
    assert any(event.event_type == "incremental.collection_completed" for event in repository.list_events(result.run_id))


@pytest.mark.parametrize("target_close", [110, 90])
def test_market_interval_includes_baseline_endpoint_when_snapshot_fails(target_close):
    def route(method, *args, **kwargs):
        if method == "get_stock_data":
            return _market_response().replace("109,111,108,110", f"{target_close},{target_close},{target_close},{target_close}")
        raise RuntimeError("snapshot unavailable")
    result = collect_us_incremental(_request(), route_to_vendor=route,
                                   now=lambda: datetime(2026, 7, 25, 2, tzinfo=UTC))
    interval = next(c.evidence.provenance["observation"]["values"] for c in result.evidence
                    if c.evidence.evidence_type == "market_interval")
    assert interval["start_session"] == "2026-07-20"
    assert interval["end_session"] == "2026-07-24"
    assert interval["completed_rows"] == 2
    assert interval["close_change"] == pytest.approx(target_close / 101 - 1)
    assert interval["min_close"] == min(101, target_close)
    assert interval["max_close"] == max(101, target_close)
    assert interval["maximum_drawdown"] == pytest.approx(min(0, target_close / 101 - 1))
