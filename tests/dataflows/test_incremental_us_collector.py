from __future__ import annotations

from datetime import UTC, date, datetime

from tests.application.test_service import _equity_resolver, _Graph, _service
from tradingagents.application.contracts import (
    AnalysisRequest,
    IncrementalCollectionRequest,
    RunStatus,
)
from tradingagents.application.incremental_collection import normalize_incremental_collection
from tradingagents.application.service import AnalysisService, default_incremental_synthesizer
from tradingagents.dataflows import incremental_us
from tradingagents.dataflows.incremental_us import collect_us_incremental
from tradingagents.provenance import ProvenanceRecord, attach_provenance


def _request(*, enabled_domains=("market",)) -> IncrementalCollectionRequest:
    return IncrementalCollectionRequest(
        version="1",
        instrument="NVDA",
        market="united_states",
        route_suffix="",
        baseline_analysis_cutoff=date(2026, 7, 20),
        analysis_cutoff=date(2026, 7, 24),
        window_start=datetime(2026, 7, 20, 23, 59, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, 23, 59, tzinfo=UTC),
        enabled_domains=enabled_domains,
        configured_routes={"data_vendors": {"core_stock_apis": "yfinance"}},
    )


def _market_response() -> str:
    body = """# Stock data for NVDA from 2026-07-01 to 2026-07-24
# Price adjustment: auto-adjusted prices (yfinance auto_adjust=True)
# Actual data source: yfinance

Date,Open,High,Low,Close,Volume
2026-07-19,99,101,98,100,1000
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
        "2026-07-19",
        "2026-07-20",
        "2026-07-24",
    ]
    assert result.stock_series.source == "yfinance"
    assert result.stock_series.adjustment_basis == "yfinance_auto_adjusted_close"
    assert result.stock_series_evidence_ref == result.evidence[0].evidence.ref
    assert result.collection_summary.domains[0].sources[0].source == "yfinance"
    assert calls[0][:2] == ("get_stock_data", ("NVDA", "2026-07-20", "2026-07-24"))


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
    node = next(node for node in repository.get_timeline("NVDA").nodes if node.id == result.run_id)
    assert node.performance.stock.status.value == "calculated"
    assert node.information_advancement.reasons == (
        "admissible_observation",
        "completed_stock_session",
    )
    assert any(event.event_type == "incremental.collection_completed" for event in repository.list_events(result.run_id))
