from __future__ import annotations

from datetime import UTC, date, datetime

from tradingagents.application.contracts import IncrementalCollectionRequest
from tradingagents.application.incremental_collection import (
    default_incremental_collector,
    normalize_incremental_collection,
)
from tradingagents.dataflows.incremental_jp import collect_japan_incremental
from tradingagents.provenance import ProvenanceRecord, attach_provenance


def _request(
    *,
    enabled_domains: tuple[str, ...] = ("market",),
    target: date = date(2026, 7, 24),
) -> IncrementalCollectionRequest:
    return IncrementalCollectionRequest(
        version="1",
        instrument="7203.T",
        market="japan",
        route_suffix=".T",
        baseline_analysis_cutoff=date(2026, 7, 17),
        analysis_cutoff=target,
        window_start=datetime(2026, 7, 17, 14, 59, 59, tzinfo=UTC),
        window_end=datetime(2026, 7, 24, 14, 59, 59, tzinfo=UTC),
        enabled_domains=enabled_domains,
        configured_routes={"data_vendors_by_market": {".T": {"core_stock_apis": "jquants,yfinance"}}},
    )


def _jquants_market_response() -> str:
    return attach_provenance(
        """# Stock data for 7203.T from 2026-07-13 to 2026-07-24
# Price adjustment: J-Quants split/dividend-adjusted close (AdjC; raw fallback unavailable for Incremental Performance)

Date,Open,High,Low,Close,Volume
2026-07-17,99,101,98,100,1000
2026-07-21,100,102,99,101,1000
2026-07-22,102,104,101,103,1000
2026-07-24,109,111,108,110,1000
""",
        ProvenanceRecord(
            evidence="get_stock_data",
            source="jquants",
            requested="2026-07-13 to 2026-07-24",
            effective="2026-07-17 to 2026-07-24",
            timing="market-date filtered",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
    )


def test_japan_collector_uses_adjusted_jquants_series_and_completed_tse_sessions() -> None:
    collected = collect_japan_incremental(
        _request(),
        route_to_vendor=lambda *_args, **_kwargs: _jquants_market_response(),
        now=lambda: datetime(2026, 7, 24, 15, 1, tzinfo=UTC),
    )

    assert collected.stock_series is not None
    assert [point.session.isoformat() for point in collected.stock_series.points] == [
        "2026-07-17",
        "2026-07-21",
        "2026-07-22",
        "2026-07-24",
    ]
    assert collected.stock_series.source == "jquants"
    assert collected.stock_series.adjustment_basis == "jquants_split_dividend_adjusted_close"
    assert collected.collection_summary.domains[0].evidence_refs == (
        collected.stock_series_evidence_ref,
    )


def test_japan_collector_omits_non_tse_rows_without_losing_the_adjusted_series() -> None:
    response = _jquants_market_response().replace(
        "2026-07-21,100,102,99,101,1000\n",
        "2026-07-20,100,102,99,101,1000\n2026-07-21,100,102,99,101,1000\n",
    )
    collected = collect_japan_incremental(
        _request(), route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 15, 1, tzinfo=UTC),
    )

    assert collected.stock_series is not None
    assert [point.session.isoformat() for point in collected.stock_series.points] == [
        "2026-07-17", "2026-07-21", "2026-07-22", "2026-07-24",
    ]
    assert collected.collection_summary.domains[0].state.value == "partial"


def test_japan_collector_retains_configured_yfinance_fallback_basis() -> None:
    response = attach_provenance(
        _jquants_market_response().split("\n", 1)[1]
        .replace("J-Quants split/dividend-adjusted close (AdjC; raw fallback unavailable for Incremental Performance)", "auto-adjusted prices (yfinance auto_adjust=True)")
        .replace("source=\"jquants\"", "source=\"yfinance\""),
        ProvenanceRecord(
            evidence="get_stock_data", source="yfinance",
            timing="fallback vendor selected; market-date filtered",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
    )
    collected = collect_japan_incremental(
        _request(), route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 15, 1, tzinfo=UTC),
    )

    assert collected.stock_series is not None
    assert collected.stock_series.source == "yfinance"
    assert collected.stock_series.fallback is True
    assert collected.stock_series.adjustment_basis == "yfinance_auto_adjusted_close"


def test_japan_collector_admits_disclosure_correction_by_publication_time() -> None:
    response = attach_provenance(
        """## 7203.T EDINET disclosures, from 2026-07-20 to 2026-07-24:

### Earnings correction (filer: Toyota)
Submitted: 2026-07-22T10:00:00+09:00
Effective period: 2026-03-31
""",
        ProvenanceRecord(
            evidence="get_news",
            source="EDINET",
            requested="2026-07-20 to 2026-07-24",
            effective="2026-07-20 to 2026-07-24",
            timing="publication/disclosure-date filtered",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
    )
    request = _request(enabled_domains=("news",))
    collected = collect_japan_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 15, 1, tzinfo=UTC),
    )
    _summary, evidence, _bindings = normalize_incremental_collection(
        request, collected, sealed_at=datetime(2026, 7, 24, 15, 1, tzinfo=UTC)
    )

    assert len(evidence) == 1
    assert evidence[0].effective_date == date(2026, 3, 31)
    assert evidence[0].available_at == datetime(2026, 7, 22, 1, tzinfo=UTC)


def test_japan_collector_keeps_actual_edinet_and_tdnet_provenance_without_empty_subfeeds() -> None:
    response = attach_provenance(
        """## 7203.T disclosures

### Statutory filing (filer: Toyota)
Submitted: 2026-07-21T15:00:00+09:00

### Timely guidance revision
Disclosed: 2026-07-22 10:00 JST
""",
        ProvenanceRecord(
            evidence="get_news", source="EDINET", timing="publication/disclosure-date filtered",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
        ProvenanceRecord(
            evidence="get_news", source="TDnet", timing="publication/disclosure-date filtered",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
        ProvenanceRecord(
            evidence="get_news", source="Google News", timing="available; no relevant items in window",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
    )
    collected = collect_japan_incremental(
        _request(enabled_domains=("news",)),
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 15, 1, tzinfo=UTC),
    )

    domain = collected.collection_summary.domains[0]
    assert [source.source for source in domain.sources] == ["edinet", "tdnet"]
    assert len(domain.evidence_refs) == 2


def test_japan_collector_parses_tdnet_pdf_suffix_without_losing_other_assembler_sources() -> None:
    response = attach_provenance(
        """## 7203.T disclosures

### Statutory filing (filer: Toyota)
Submitted: 2026-07-21T15:00:00+09:00

### Timely guidance revision
Disclosed: 2026-07-22 10:00 JST · PDF: https://www.release.tdnet.info/inbs/example.pdf
""",
        ProvenanceRecord(
            evidence="get_news", source="EDINET", timing="publication/disclosure-date filtered",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
        ProvenanceRecord(
            evidence="get_news", source="TDnet", timing="publication/disclosure-date filtered",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
    )
    collected = collect_japan_incremental(
        _request(enabled_domains=("news",)),
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 15, 1, tzinfo=UTC),
    )

    domain = collected.collection_summary.domains[0]
    assert domain.diagnostic is not None
    assert domain.diagnostic.code == "bounded_japanese_news_feed"
    assert [source.source for source in domain.sources] == ["edinet", "tdnet"]
    assert len(domain.evidence_refs) == 2


def test_japan_collector_admits_published_yfinance_fallback_news() -> None:
    response = attach_provenance(
        """### [direct] Toyota raises outlook (source: Reuters)
Published: 2026-07-22T01:00:00Z
New guidance was published in the Incremental window.
""",
        ProvenanceRecord(
            evidence="get_news", source="yfinance",
            timing="fallback vendor selected; publication-date filtered",
            retrieved_at="2026-07-24T15:00:00Z",
        ),
    )
    request = _request(enabled_domains=("news",))
    collected = collect_japan_incremental(
        request, route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 24, 15, 1, tzinfo=UTC),
    )
    _summary, evidence, _bindings = normalize_incremental_collection(
        request, collected, sealed_at=datetime(2026, 7, 24, 15, 1, tzinfo=UTC)
    )

    assert [item.source for item in evidence] == ["yfinance"]
    assert evidence[0].available_at == datetime(2026, 7, 22, 1, tzinfo=UTC)
    assert evidence[0].fallback is True


def test_japan_collector_labels_live_fundamentals_near_live_and_omits_them_after_five_days() -> None:
    response = attach_provenance(
        "# Requested analysis date: 2026-07-24\n# Retrieved at: 2026-07-30T00:00:00Z\nLive analyst consensus snapshot",
        ProvenanceRecord(
            evidence="get_fundamentals",
            source="yfinance",
            requested="2026-07-24",
            effective="live retrieval",
            timing="live non-point-in-time",
            retrieved_at="2026-07-30T00:00:00Z",
        ),
    )
    request = _request(enabled_domains=("fundamentals",), target=date(2026, 7, 25))
    collected = collect_japan_incremental(
        request,
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 30, 0, 1, tzinfo=UTC),
    )
    summary, evidence, _bindings = normalize_incremental_collection(
        request, collected, sealed_at=datetime(2026, 7, 30, 0, 1, tzinfo=UTC)
    )
    assert summary.domains[0].temporal_bases == ("near_live_advisory",)
    assert len(evidence) == 1

    old_request = _request(enabled_domains=("fundamentals",), target=date(2026, 7, 23))
    old_collected = collect_japan_incremental(
        old_request,
        route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 30, 0, 1, tzinfo=UTC),
    )
    old_summary, old_evidence, _bindings = normalize_incremental_collection(
        old_request, old_collected, sealed_at=datetime(2026, 7, 30, 0, 1, tzinfo=UTC)
    )
    assert old_summary.domains[0].state.value == "empty"
    assert old_summary.domains[0].omitted_by_temporal_boundary is True
    assert old_evidence == ()


def test_japan_collector_keeps_pit_fundamentals_when_a_nested_live_span_ages_out() -> None:
    response = attach_provenance(
        """# Fundamentals overview for 7203.T (J-Quants summary)
Latest disclosure: FY end 2026-03-31 (disclosed 2026-07-22; Consolidated, Japanese GAAP)
Effective period: 2026-03-31
Official correction published after the Full Baseline.

<!-- tradingagents-evidence-span:v1 {\"temporal_scope\":\"live_only\"} --><!-- tradingagents-provenance:v1 {\"evidence\":\"get_fundamentals\",\"source\":\"yfinance analyst consensus\",\"requested\":\"2026-07-24\",\"effective\":\"retrieval-time analyst snapshot\",\"timing\":\"live non-point-in-time\",\"retrieved_at\":\"2026-07-30T00:00:00Z\"} -->- Forward PE: analyst consensus live only<!-- /tradingagents-evidence-span:v1 -->
""",
        ProvenanceRecord(
            evidence="get_fundamentals", source="J-Quants official summary",
            timing="disclosure-date filtered", retrieved_at="2026-07-30T00:00:00Z",
        ),
        ProvenanceRecord(
            evidence="get_fundamentals", source="J-Quants adjusted OHLCV",
            timing="market-date filtered", retrieved_at="2026-07-30T00:00:00Z",
        ),
    )
    request = _request(enabled_domains=("fundamentals",), target=date(2026, 7, 24))
    collected = collect_japan_incremental(
        request, route_to_vendor=lambda *_args, **_kwargs: response,
        now=lambda: datetime(2026, 7, 30, 0, 1, tzinfo=UTC),
    )
    summary, evidence, _bindings = normalize_incremental_collection(
        request, collected, sealed_at=datetime(2026, 7, 30, 0, 1, tzinfo=UTC)
    )

    assert [item.source for item in evidence] == ["j-quants_official_summary"]
    assert evidence[0].effective_date == date(2026, 3, 31)
    assert evidence[0].available_at == datetime(2026, 7, 22, 14, 59, 59, 999999, tzinfo=UTC)
    assert {source.source for source in summary.domains[0].sources} == {
        "j-quants_official_summary", "j-quants_adjusted_ohlcv", "yfinance_analyst_consensus",
    }
    assert summary.domains[0].omitted_by_temporal_boundary is True


def test_default_collector_dispatches_japan_path(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(
        "tradingagents.dataflows.incremental_jp.collect_japan_incremental",
        lambda request: sentinel,
    )
    assert default_incremental_collector(_request()) is sentinel
