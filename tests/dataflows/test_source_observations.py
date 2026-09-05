from datetime import UTC, date, datetime

import pandas as pd

from tradingagents.dataflows import y_finance


def test_us_statement_exposes_period_values_without_claiming_filing_date(monkeypatch):
    from tradingagents.dataflows.source_observations import capture_observations

    class Stock:
        quarterly_cashflow = pd.DataFrame(
            {pd.Timestamp("2026-06-30"): [150.0, -100.0]},
            index=["Operating Cash Flow", "Capital Expenditure"],
        )

    monkeypatch.setattr(y_finance.yf, "Ticker", lambda _: Stock())
    with capture_observations() as observations:
        output = y_finance.get_cashflow("GOOG", "quarterly", "2026-09-05")
    assert "150" in output
    assert len(observations) == 1
    observation = observations[0]
    assert observation.effective_date == date(2026, 6, 30)
    assert observation.available_at is None
    assert observation.available_on is None
    assert observation.values["Capital Expenditure"] == -100.0
    assert observation.retrieved_at.tzinfo is not None


def test_china_statement_observations_retain_visibility_and_cumulative_basis(monkeypatch):
    from tests.cn.test_cn_statements import _frame
    from tradingagents.dataflows.cn import cn_statements
    from tradingagents.dataflows.source_observations import capture_observations

    monkeypatch.setattr(cn_statements, "fetch_finance_records", lambda *_: ("600309.SS", _frame()))
    monkeypatch.setattr(cn_statements, "get_company_profile", lambda _: pd.DataFrame())
    monkeypatch.setattr(cn_statements, "get_statement_frame", lambda *_: None)
    with capture_observations() as observations:
        cn_statements.get_income_statement("600309.SS", curr_date="2026-03-21")
    assert observations[0].available_on == date(2026, 3, 21)
    assert observations[0].values["period_basis"] == "YTD"
    assert observations[0].values["Revenue"] == 1000


def test_japan_margin_publishes_conservative_release_date(monkeypatch):
    from tradingagents.dataflows.jp import jquants_sentiment
    from tradingagents.dataflows.source_observations import capture_observations

    monkeypatch.setattr(
        jquants_sentiment,
        "fetch_records",
        lambda *a: [
            {"Date": "2026-08-28", "LongVol": 90, "ShrtVol": 10},
        ],
    )
    with capture_observations() as observations:
        jquants_sentiment.get_margin_balance("9984.T", "2026-09-05")
    assert observations[0].effective_date == date(2026, 8, 28)
    assert observations[0].available_on == date(2026, 9, 1)
    assert "T+2" in observations[0].timing


def test_incremental_admits_statement_rows_from_the_shared_producer(monkeypatch):
    from tests.dataflows.test_incremental_us_collector import _request
    from tradingagents.application.incremental_collection import normalize_incremental_collection
    from tradingagents.dataflows.incremental_us import collect_us_incremental
    from tradingagents.provenance import ProvenanceRecord, attach_provenance

    class Stock:
        quarterly_cashflow = pd.DataFrame(
            {pd.Timestamp("2026-06-30"): [150.0, -100.0]},
            index=["Operating Cash Flow", "Capital Expenditure"],
        )

    monkeypatch.setattr(y_finance.yf, "Ticker", lambda _: Stock())

    def route(method, *args, **kwargs):
        if method == "get_cashflow":
            return y_finance.get_cashflow(*args)
        return attach_provenance(
            "live overview",
            ProvenanceRecord(
                evidence=method,
                source="yfinance",
                requested="2026-09-05",
                effective="live snapshot",
                timing="live non-point-in-time",
            ),
        )

    now = datetime.now(UTC)
    request = _request(
        enabled_domains=("fundamentals",),
        baseline=date(2026, 8, 29),
        target=now.date(),
        window_start=datetime(2026, 8, 29, tzinfo=UTC),
        window_end=now,
    )
    result = collect_us_incremental(request, route_to_vendor=route)
    _, items, _ = normalize_incremental_collection(request, result, sealed_at=datetime.now(UTC))
    statement = next(item for item in items if item.evidence_type == "financial_cashflow")
    assert '"Capital Expenditure": -100.0' in statement.content
    assert statement.origins[0].temporal_scope.value == "live_only"


def test_professional_signal_enters_incremental_and_full_with_same_identity(monkeypatch):
    from tests.dataflows.test_incremental_jp_collector import _request
    from tradingagents.application.incremental_collection import normalize_incremental_collection
    from tradingagents.dataflows import incremental_jp
    from tradingagents.dataflows.market_signals import (
        FetchedSentimentSignal,
        sentiment_signal_specs,
    )
    from tradingagents.dataflows.source_observations import SourceObservation
    from tradingagents.graph.research_graph import _collect_evidence

    observed = SourceObservation(
        "J-Quants",
        "margin_balances",
        "2026-07-17",
        {"LongVol": 90, "ShrtVol": 10},
        datetime(2026, 7, 24, 6, tzinfo=UTC),
        effective_date=date(2026, 7, 17),
        available_on=date(2026, 7, 21),
        timing="inferred T+2 publication",
    )
    fetched = FetchedSentimentSignal(
        sentiment_signal_specs("7203.T")[1], "margin", observations=(observed,)
    )
    monkeypatch.setattr(incremental_jp, "fetch_sentiment_signals", lambda *a: (fetched,))
    request = _request(enabled_domains=("social",))
    result = incremental_jp.collect_japan_incremental(request)
    _, admitted, _ = normalize_incremental_collection(
        request,
        result,
        sealed_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    full = _collect_evidence(
        [],
        "",
        requested_date=request.analysis_cutoff,
        analyst="social",
        prefetched_blocks=[{"source_observation": observed.dump()}],
        instrument=request.instrument,
    )
    assert len(admitted) == 1
    assert (
        admitted[0].provenance["observation_identity"] == full[0].provenance["observation_identity"]
    )


def test_financial_release_keeps_older_comparative_periods_as_context():
    from tradingagents.dataflows.financial_inputs import collect_financial_inputs
    from tradingagents.dataflows.source_observations import publish_observation

    def route(method, *_args, **_kwargs):
        if method == "get_income_statement":
            for period, visible, value in (("2026-06-30", "2026-08-25", 20), ("2026-03-31", "2026-04-25", 10)):
                publish_observation("Sina", "financial_income", period,
                                    {"Revenue": value, "period_basis": "YTD"},
                                    effective_date=period, available_on=visible)
        return "source response"

    result = collect_financial_inputs("600309.SS", "2026-09-05", route=route, include_overview=False)
    assert len(result["observations"]) == 1
    observation = result["observations"][0]
    assert observation["available_on"] == "2026-08-25"
    assert [period["values"]["Revenue"] for period in observation["values"]["periods"]] == [20, 10]


def test_financial_rate_limit_preserves_an_earlier_success():
    from tradingagents.dataflows.errors import VendorRateLimitError
    from tradingagents.dataflows.financial_inputs import collect_financial_inputs
    from tradingagents.dataflows.source_observations import publish_observation

    calls = []
    def route(method, *_args, **_kwargs):
        calls.append(method)
        if method == "get_balance_sheet":
            raise VendorRateLimitError("limited")
        publish_observation("Yahoo", "financial_income", "GOOG:2026-06-30", {"Revenue": 42})
        return "success"

    result = collect_financial_inputs("GOOG", "2026-09-05", route=route,
                                      include_overview=False, stop_on_rate_limit=True)
    assert len(result["observations"]) == 1
    assert calls == ["get_income_statement", "get_balance_sheet"]


def test_cn_news_signal_deduplication_preserves_valid_domain_contracts():
    from tests.dataflows.test_incremental_cn_collector import _request
    from tradingagents.application.contracts import CollectionDiagnostic, CollectionDomainResult
    from tradingagents.dataflows.incremental_inputs import augment_domain, dedupe_news_domains
    from tradingagents.dataflows.source_observations import SourceObservation

    request = _request(enabled_domains=("news", "social"))
    observed = SourceObservation("CNINFO", "news_article", "record-1",
                                 {"title": "event", "url": "https://example.test/1"},
                                 datetime(2026, 7, 24, tzinfo=UTC), available_on=date(2026, 7, 21))
    domains, candidates = [], []
    for role in ("news", "social"):
        empty = CollectionDomainResult(domain=role, state="unavailable", diagnostic=CollectionDiagnostic(code="test"))
        domain, values = augment_domain(request, empty, [observed])
        domains.append(domain)
        candidates.extend(values)
    domains, candidates = dedupe_news_domains(domains, candidates)
    assert len(candidates) == 1
    assert not domains[1].evidence_refs
    for domain in domains:
        CollectionDomainResult.model_validate(domain.model_dump())


def test_full_statement_evidence_resolves_publication_day_in_its_market():
    from zoneinfo import ZoneInfo

    import pytest

    from tradingagents.application.contracts import EvidenceBundle
    from tradingagents.dataflows.source_observations import SourceObservation
    from tradingagents.graph.research_graph import _collect_evidence

    observation = SourceObservation("J-Quants", "financial_income", "9984.T:2026-06-30",
                                    {"Revenue": 100}, datetime(2026, 9, 5, tzinfo=UTC),
                                    effective_date=date(2026, 6, 30), available_on=date(2026, 9, 5))
    items = _collect_evidence([], "", requested_date=date(2026, 9, 5), analyst="fundamentals",
                              prefetched_blocks=[{"source_observation": observation.dump()}],
                              instrument="9984.T")
    assert items[0].available_at == datetime(2026, 9, 5, 23, 59, 59, 999999, tzinfo=ZoneInfo("Asia/Tokyo"))
    with pytest.raises(ValueError, match="after the analysis cutoff"):
        EvidenceBundle(instrument="9984.T", analysis_date=date(2026, 9, 4), items=tuple(items))


def test_routed_fallback_news_has_one_consistent_full_observation(monkeypatch):
    from langchain_core.messages import ToolMessage

    from tradingagents.dataflows import interface
    from tradingagents.dataflows.config import get_config, use_config
    from tradingagents.dataflows.errors import NoMarketDataError
    from tradingagents.dataflows.news_selection import (
        NewsCandidate,
        finalize_news,
        render_candidate,
    )
    from tradingagents.dataflows.source_observations import (
        capture_observations,
        publish_observation,
    )
    from tradingagents.graph.research_graph import _collect_evidence

    current = datetime(2026, 9, 5, 10, tzinfo=UTC)
    def failed(*_a, **_k):
        publish_observation("alpha_vantage", "news_article", "discard", {"title": "partial"}, retrieved_at=current)
        raise NoMarketDataError("GOOG")
    def fallback(*_a, **_k):
        row = NewsCandidate("yfinance", "event", "### event", published="2026-09-04T10:00:00Z",
                            retrieved_at=current.isoformat())
        return finalize_news("## news\n\n" + render_candidate(row), "yfinance", "GOOG", "2026-09-01", "2026-09-05", 30)
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_news", {"alpha_vantage": failed, "yfinance": fallback})
    with use_config({**get_config(), "tool_vendors": {"get_news": "alpha_vantage,yfinance"}}), capture_observations() as observed:
        body = interface.route_to_vendor("get_news", "GOOG", "2026-09-01", "2026-09-05", _provenance=True)
    assert len(observed) == 1
    assert observed[0].fallback
    sealed = _collect_evidence([ToolMessage(content=body, name="get_news", tool_call_id="news")], "",
                               requested_date=current.date(), analyst="news", instrument="GOOG")
    combined = {item.ref: item for item in [*sealed, *(o.evidence(current.date(), instrument="GOOG") for o in observed)]}
    assert len(combined) == 1
    assert all(item.fallback and item.origins[0].fallback for item in combined.values())


def test_routed_snapshot_retains_fallback_at_producer_boundary(monkeypatch):
    from tradingagents.dataflows import interface
    from tradingagents.dataflows.config import get_config, use_config
    from tradingagents.dataflows.errors import NoMarketDataError
    from tradingagents.dataflows.source_observations import (
        capture_observations,
        publish_observation,
    )

    def failed(*_a, **_k):
        raise NoMarketDataError("GOOG")
    def snapshot(*_a, **_k):
        publish_observation("yfinance", "verified_market_snapshot", "GOOG", {"close": 100})
        return "snapshot"
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_verified_market_snapshot", {"alpha_vantage": failed, "yfinance": snapshot})
    with use_config({**get_config(), "tool_vendors": {"get_verified_market_snapshot": "alpha_vantage,yfinance"}}), capture_observations() as observed:
        interface.route_to_vendor("get_verified_market_snapshot", "GOOG", "2026-09-05", 5, _provenance=True)
    assert len(observed) == 1 and observed[0].fallback


def test_cn_news_deduplication_preserves_failures_for_partial_and_empty_social():
    from tests.dataflows.test_incremental_cn_collector import _request
    from tradingagents.application.contracts import (
        CollectionDiagnostic,
        CollectionDomainResult,
        CollectionSourceProvenance,
        CollectionSummary,
        IncrementalCollectionResult,
    )
    from tradingagents.application.incremental_collection import normalize_incremental_collection
    from tradingagents.dataflows.incremental_inputs import augment_domain, dedupe_news_domains
    from tradingagents.dataflows.source_observations import SourceObservation

    request = _request(enabled_domains=("news", "social"))
    retrieved = datetime(2026, 7, 24, tzinfo=UTC)
    announcement = SourceObservation("CNINFO", "news_article", "one", {"title": "event", "link": "https://example.test/1"},
                                     retrieved, available_on=date(2026, 7, 21))
    rating = SourceObservation("Eastmoney", "analyst_rating", "two", {"rating": "buy"}, retrieved,
                               available_on=date(2026, 7, 22), fallback=True)
    for keep_rating in (True, False):
        domains, candidates = [], []
        for role, observations in (("news", [announcement]), ("social", [announcement] + ([rating] if keep_rating else []))):
            empty = CollectionDomainResult(domain=role, state="unavailable", diagnostic=CollectionDiagnostic(code="test"))
            domain, extra = augment_domain(request, empty, observations)
            domains.append(domain)
            candidates.extend(extra)
        social = domains[1]
        social = social.model_copy(update={
            "diagnostic": CollectionDiagnostic(code="professional_signals_partial"),
            "sources": tuple(source.model_copy(update={"diagnostic": CollectionDiagnostic(code="upstream_source_partial")})
                             for source in social.sources) + (
                CollectionSourceProvenance(source="sse", retrieved_at=retrieved,
                                           diagnostic=CollectionDiagnostic(code="upstream_source_unavailable")),
            ),
        })
        domains, candidates = dedupe_news_domains([domains[0], social], candidates)
        result = IncrementalCollectionResult(collection_summary=CollectionSummary(version="1", market=request.market, domains=tuple(domains)),
                                             evidence=tuple(candidates))
        summary, _, _ = normalize_incremental_collection(request, result, sealed_at=request.window_end)
        social = summary.domains[1]
        sources = {source.source: source for source in social.sources}
        assert sources["sse"].diagnostic.code == "upstream_source_unavailable"
        assert sources["cninfo"].diagnostic.code == "upstream_source_partial"
        assert "professional_signals_partial" in social.diagnostic.code
        if keep_rating:
            assert len(social.evidence_refs) == 1
            assert sources["eastmoney"].diagnostic.code == "upstream_source_partial"
            assert sources["eastmoney"].retrieved_at == retrieved and sources["eastmoney"].fallback
        else:
            assert not social.evidence_refs
            assert "articles_already_in_news" in social.diagnostic.code
