from __future__ import annotations

from datetime import UTC, date, datetime

from tradingagents.application.contracts import (
    IncrementalCollectionRequest,
    PerformanceComponentStatus,
)
from tradingagents.application.incremental_collection import (
    calculate_stock_performance,
    default_incremental_collector,
    normalize_incremental_collection,
)
from tradingagents.dataflows.cn import calendar
from tradingagents.dataflows.incremental_cn import collect_mainland_china_incremental
from tradingagents.provenance import ProvenanceRecord, attach_evidence_span, attach_provenance


def _request(
    *,
    enabled_domains: tuple[str, ...] = ("market",),
    target: date = date(2026, 7, 24),
) -> IncrementalCollectionRequest:
    return IncrementalCollectionRequest(
        version="1",
        instrument="600519.SS",
        market="mainland_china",
        route_suffix=".SS",
        baseline_analysis_cutoff=date(2026, 7, 17),
        analysis_cutoff=target,
        window_start=datetime(2026, 7, 17, 15, 59, 59, tzinfo=UTC),
        window_end=datetime(
            target.year,
            target.month,
            target.day,
            15,
            59,
            59,
            tzinfo=UTC,
        ),
        enabled_domains=enabled_domains,
        configured_routes={
            "data_vendors_by_market": {
                ".SS": {"core_stock_apis": "akshare,yfinance"}
            }
        },
    )


def _tencent_market_response() -> str:
    return attach_provenance(
        """# Stock data for 600519.SS from 2026-07-17 to 2026-07-24
# Price adjustment: qfq (forward-adjusted)
# Actual data source: AkShare / Tencent

Date,Open,High,Low,Close,Volume
2026-07-17,99,101,98,100,1000
2026-07-20,100,102,99,101,1000
2026-07-22,102,104,101,103,1000
2026-07-24,109,111,108,110,1000
""",
        ProvenanceRecord(
            evidence="get_stock_data",
            source="AkShare / Tencent",
            requested="2026-07-17 to 2026-07-24",
            effective="2026-07-17 to 2026-07-24",
            timing="market-date filtered; qfq adjusted; future rows excluded",
            retrieved_at="2026-07-24T08:00:00Z",
        ),
    )


def test_mainland_collector_uses_one_qfq_series_and_completed_sessions(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        calendar,
        "trading_dates",
        lambda: tuple(
            date(2026, 7, day) for day in (17, 20, 21, 22, 23, 24)
        ),
    )

    collected = collect_mainland_china_incremental(
        _request(),
        route_to_vendor=lambda *_args, **_kwargs: _tencent_market_response(),
        now=lambda: datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )

    assert collected.stock_series is not None
    assert [point.session.isoformat() for point in collected.stock_series.points] == [
        "2026-07-17",
        "2026-07-20",
        "2026-07-22",
        "2026-07-24",
    ]
    assert collected.stock_series.source == "akshare_tencent"
    assert collected.stock_series.adjustment_basis == "qfq_forward_adjusted"
    assert collected.collection_summary.domains[0].evidence_refs == (
        collected.stock_series_evidence_ref,
    )
    performance = calculate_stock_performance(_request(), collected.stock_series)
    assert performance.stock.calculation is not None
    assert performance.stock.calculation.start_value == 100
    assert performance.stock.calculation.end_value == 110


def test_mainland_collector_retains_internal_eastmoney_fallback_provenance(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        calendar,
        "trading_dates",
        lambda: tuple(date(2026, 7, day) for day in (17, 20, 21, 22, 23, 24)),
    )
    response = _tencent_market_response().replace(
        '"source":"AkShare / Tencent"',
        '"source":"AkShare / Eastmoney"',
    ).replace(
        '"timing":"market-date filtered; qfq adjusted; future rows excluded"',
        '"timing":"market-date filtered; qfq adjusted; fallback: Tencent primary retrieval unavailable"',
    ).replace(
        "# Actual data source: AkShare / Tencent",
        "# Actual data source: AkShare / Eastmoney",
    )

    collected = collect_mainland_china_incremental(
        _request(),
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )

    assert collected.stock_series is not None
    assert collected.stock_series.source == "akshare_eastmoney"
    assert collected.stock_series.fallback is True
    assert collected.collection_summary.domains[0].sources[0].fallback is True


def test_default_collector_dispatches_mainland_path(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(
        "tradingagents.dataflows.incremental_cn.collect_mainland_china_incremental",
        lambda request: sentinel,
    )

    assert default_incremental_collector(_request()) is sentinel


def test_mainland_collector_admits_later_published_cninfo_correction() -> None:
    response = attach_evidence_span(
        attach_provenance(
            """## 600519.SS company announcements (CNINFO)

### [direct] Annual-report correction
Disclosed: 2026-07-22 10:00 CST
Effective period: 2025-12-31
""",
            ProvenanceRecord(
                evidence="get_news",
                source="CNINFO",
                requested="2026-07-17 to 2026-07-24",
                effective="2026-07-17 to 2026-07-24",
                timing="publication-date filtered; returned_items=1",
                retrieved_at="2026-07-24T08:00:00Z",
            ),
        ),
        temporal_scope="point_in_time",
    )

    collected = collect_mainland_china_incremental(
        _request(enabled_domains=("news",)),
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )

    assert len(collected.evidence) == 1
    item = collected.evidence[0].evidence
    assert item.source == "cninfo"
    assert item.effective_date == date(2025, 12, 31)
    assert item.available_at == datetime(2026, 7, 22, 2, tzinfo=UTC)
    domain = collected.collection_summary.domains[0]
    assert domain.state.value == "partial"
    assert domain.temporal_bases == ("pit",)
    assert domain.diagnostic is not None
    assert domain.diagnostic.code == "bounded_mainland_news_feed"


def test_mainland_collector_discloses_source_omitted_by_global_news_cap() -> None:
    admitted = attach_evidence_span(
        attach_provenance(
            """## CNINFO

### [direct] Official filing
Disclosed: 2026-07-22 10:00 CST
""",
            ProvenanceRecord(
                evidence="get_news",
                source="CNINFO",
                requested="2026-07-17 to 2026-07-24",
                effective="2026-07-17 to 2026-07-24",
                timing=(
                    "publication-date filtered; returned_items=1; "
                    "duplicate_items=0; kept_items=1; shared_limit=1"
                ),
                retrieved_at="2026-07-24T08:00:00Z",
            ),
        ),
        temporal_scope="point_in_time",
    )
    omitted = attach_provenance(
        "",
        ProvenanceRecord(
            evidence="get_news",
            source="Eastmoney Research",
            requested="2026-07-17 to 2026-07-24",
            effective="2026-07-17 to 2026-07-24",
            timing=(
                "publication-date filtered; returned_items=1; "
                "duplicate_items=0; kept_items=0; shared_limit=1; "
                "truncated_by_global_cap=1"
            ),
            retrieved_at="2026-07-24T08:00:00Z",
        ),
    )

    collected = collect_mainland_china_incremental(
        _request(enabled_domains=("news",)),
        route_to_vendor=lambda *_args, **_kwargs: f"{admitted}\n\n{omitted}",
        now=lambda: datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )

    domain = collected.collection_summary.domains[0]
    sources = {source.source: source for source in domain.sources}
    assert domain.diagnostic is not None
    assert domain.diagnostic.code == "bounded_mainland_news_feed_with_global_cap"
    assert sources["eastmoney_research"].diagnostic is not None
    assert sources["eastmoney_research"].diagnostic.code == "truncated_by_global_cap"


def test_mainland_collector_preserves_pit_and_near_live_fundamentals() -> None:
    pit = attach_evidence_span(
        attach_provenance(
            """# China A-share Fundamentals for 600519.SS
## Financial abstract (AkShare / Sina)
Latest visible disclosure/update: 2026-07-22
Effective period: 2025-12-31
Basic EPS: 2.0
""",
            ProvenanceRecord(
                evidence="get_fundamentals",
                source="AkShare / Sina financial abstract",
                requested="2026-07-24",
                effective="2026-07-22",
                timing="publication/update-date filtered; later conflicting date wins",
                retrieved_at="2026-07-24T08:00:00Z",
            ),
        ),
        temporal_scope="point_in_time",
    )
    live = attach_evidence_span(
        attach_provenance(
            """## Company profile (CNINFO; current reference, not historical PIT)
主营业务: 白酒生产
""",
            ProvenanceRecord(
                evidence="get_fundamentals",
                source="AkShare / CNINFO company profile",
                requested="2026-07-24",
                effective="current reference",
                timing="live-only current company reference; not historical PIT",
                retrieved_at="2026-07-24T08:00:00Z",
            ),
        ),
        temporal_scope="live_only",
    )
    request = _request(enabled_domains=("fundamentals",))
    collected = collect_mainland_china_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: f"{live}\n\n{pit}",
        now=lambda: datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )
    summary, evidence, _bindings = normalize_incremental_collection(
        request,
        collected,
        sealed_at=datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )

    assert len(evidence) == 2
    pit_item = next(item for item in evidence if item.source == "akshare_sina_financial_abstract")
    assert pit_item.effective_date == date(2025, 12, 31)
    assert pit_item.available_at == datetime(2026, 7, 22, 15, 59, 59, 999999, tzinfo=UTC)
    live_item = next(item for item in evidence if item.source == "akshare_cninfo_company_profile")
    assert live_item.available_at is None
    assert live_item.origins[0].temporal_scope == "live_only"
    domain = summary.domains[0]
    assert domain.state.value == "partial"
    assert domain.temporal_bases == ("near_live_advisory", "pit")
    assert domain.diagnostic is not None
    assert domain.diagnostic.code == "mixed_pit_and_near_live_fundamentals"


def test_mainland_collector_omits_six_day_old_live_snapshot() -> None:
    response = attach_evidence_span(
        attach_provenance(
            "## Current valuation snapshot\nPE: 20",
            ProvenanceRecord(
                evidence="get_fundamentals",
                source="yfinance current valuation snapshot",
                requested="2026-07-18",
                effective="current reference",
                timing="current-only snapshot; not historical PIT",
                retrieved_at="2026-07-24T08:00:00Z",
            ),
        ),
        temporal_scope="live_only",
    )
    request = _request(
        enabled_domains=("fundamentals",),
        target=date(2026, 7, 18),
    )
    collected = collect_mainland_china_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )
    summary, evidence, _bindings = normalize_incremental_collection(
        request,
        collected,
        sealed_at=datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )

    assert evidence == ()
    assert summary.domains[0].state.value == "empty"
    assert summary.domains[0].omitted_by_temporal_boundary is True


def test_mainland_collector_supports_shenzhen_a_share_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        calendar,
        "trading_dates",
        lambda: tuple(date(2026, 7, day) for day in (17, 20, 21, 22, 23, 24)),
    )
    request = _request().model_copy(
        update={"instrument": "000001.SZ", "route_suffix": ".SZ"}
    )
    response = _tencent_market_response().replace("600519.SS", "000001.SZ")

    collected = collect_mainland_china_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 8, 1, tzinfo=UTC),
    )

    assert collected.stock_series is not None
    assert collected.stock_series.instrument == "000001.SZ"


def test_mainland_calendar_uses_prior_completed_session_and_not_yet_observable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        calendar,
        "trading_dates",
        lambda: (date(2026, 4, 3), date(2026, 4, 7), date(2026, 4, 8)),
    )
    request = IncrementalCollectionRequest(
        version="1",
        instrument="600519.SS",
        market="mainland_china",
        route_suffix=".SS",
        baseline_analysis_cutoff=date(2026, 4, 3),
        analysis_cutoff=date(2026, 4, 6),
        window_start=datetime(2026, 4, 3, 15, 59, 59, tzinfo=UTC),
        window_end=datetime(2026, 4, 6, 15, 59, 59, tzinfo=UTC),
        enabled_domains=("market",),
        configured_routes={},
    )
    calls = []
    response = attach_provenance(
        """# Stock data for 600519.SS from 2026-04-03 to 2026-04-06
# Price adjustment: qfq (forward-adjusted)

Date,Open,High,Low,Close,Volume
2026-04-03,99,101,98,100,1000
""",
        ProvenanceRecord(
            evidence="get_stock_data",
            source="AkShare / Tencent",
            requested="2026-04-03 to 2026-04-06",
            effective="2026-04-03",
            timing="market-date filtered; qfq adjusted",
            retrieved_at="2026-04-06T08:00:00Z",
        ),
    )

    def route(method, *args, **kwargs):
        calls.append((method, args, kwargs))
        return response

    collected = collect_mainland_china_incremental(
        request,
        route_to_vendor=route,
        now=lambda: datetime(2026, 4, 6, 8, 1, tzinfo=UTC),
    )

    assert calls[0][1][1] == "2026-04-03"
    performance = calculate_stock_performance(request, collected.stock_series)
    assert performance.stock.status is PerformanceComponentStatus.NOT_YET_OBSERVABLE
