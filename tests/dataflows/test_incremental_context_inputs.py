import json
from datetime import UTC, date, datetime

from tests.data_policy import request_context
from tradingagents.data.macro_common import SeriesCache
from tradingagents.domain.data_result import DataResult


def test_macro_cache_preserves_original_retrieval_across_instances(tmp_path, monkeypatch):
    from tests.data_policy import data_config, data_policy

    with data_policy({**data_config(), "data_cache_dir": str(tmp_path)}):
        data = {"points": [("2026-08-01", "2.5")]}
        key = ("policy", date.today().isoformat(), 30)
        first = SeriesCache(namespace="observation-test")
        first.put_observation(key, data, data_context=request_context())
        original = data["retrieved_at"]
        second = SeriesCache(namespace="observation-test")
        assert second.get(key, data_context=request_context())["retrieved_at"] == original


def test_same_structured_observation_does_not_advance_after_refresh():
    from dataclasses import replace

    from tradingagents.domain.data import SourceObservation
    from tradingagents.domain.performance import PerformanceComponent, PerformanceObservation
    from tradingagents.research.incremental.collection import assess_information_advancement

    original = SourceObservation(
        "FRED", "macro_indicator", "rate", {"value": 4.5}, datetime(2026, 9, 4, tzinfo=UTC)
    )
    fresh = replace(original, retrieved_at=datetime(2026, 9, 5, tzinfo=UTC))
    result = assess_information_advancement(
        baseline_items=(original.evidence(date(2026, 9, 4)),),
        current_items=(fresh.evidence(date(2026, 9, 5)),),
        performance=PerformanceObservation(
            stock=PerformanceComponent(status="unavailable", reason="test")
        ),
        stock_series_admitted=False,
    )
    assert not result.advanced


def test_macro_window_display_change_reaches_evidence_without_advancing(monkeypatch):
    from tradingagents.data import fred, macro_panel
    from tradingagents.domain.performance import PerformanceComponent, PerformanceObservation
    from tradingagents.research.incremental.collection import assess_information_advancement

    retrieved_at = datetime(2026, 9, 5, tzinfo=UTC)

    def produce(points):
        monkeypatch.setattr(
            fred,
            "fetch_series",
            lambda *_args, data_context: {
                "points": points,
                "units": "Percent",
                "frequency": "Daily",
                "retrieved_at": retrieved_at.isoformat(),
            },
        )
        result = macro_panel._cell(("fred", "VIXCLS"), "2026-09-05", data_context=request_context())
        return result.observations[0].evidence(date(2026, 9, 5))

    baseline = produce([("2025-09-01", "10"), ("2026-09-04", "15")])
    current = produce([("2025-09-02", "12"), ("2026-09-04", "15")])

    assert '"display": "15 (2026-09-04, Δ +5.00, +50.0%)"' in baseline.content
    assert '"display": "15 (2026-09-04, Δ +3.00, +25.0%)"' in current.content
    result = assess_information_advancement(
        baseline_items=(baseline,),
        current_items=(current,),
        performance=PerformanceObservation(
            stock=PerformanceComponent(status="unavailable", reason="test")
        ),
        stock_series_admitted=False,
    )
    assert not result.advanced


def test_exact_yoy_comparator_reaches_evidence_and_revision_advances(monkeypatch):
    from tradingagents.data import fred, macro_panel
    from tradingagents.domain.performance import PerformanceComponent, PerformanceObservation
    from tradingagents.research.incremental.collection import assess_information_advancement

    def produce(base_value):
        monkeypatch.setattr(
            fred,
            "fetch_series",
            lambda *_args, data_context: {
                "points": [("2025-06-01", base_value), ("2026-06-01", "105")],
                "units": "Index 1982-1984=100",
                "frequency": "Monthly",
                "retrieved_at": datetime(2026, 9, 5, tzinfo=UTC).isoformat(),
            },
        )
        result = macro_panel._cell(
            ("fred", "cpi", "exact_yoy", 550), "2026-09-05", data_context=request_context()
        )
        return result.observations[0].evidence(date(2026, 9, 5))

    baseline = produce("100")
    current = produce("101")

    baseline_values = json.loads(baseline.content.split("\n", 1)[1])
    assert baseline_values["display"] == "+5.0% YoY (2026-06-01)"
    assert baseline_values["year_over_year"] == {
        "base_date": "2025-06-01",
        "base_value": "100",
        "current_date": "2026-06-01",
        "current_value": "105",
        "pct": 5.0,
    }
    result = assess_information_advancement(
        baseline_items=(baseline,),
        current_items=(current,),
        performance=PerformanceObservation(
            stock=PerformanceComponent(status="unavailable", reason="test")
        ),
        stock_series_admitted=False,
    )
    assert result.advanced


def test_partial_background_preserves_cached_time_and_failed_source(monkeypatch):
    from tests.dataflows.test_incremental_us_collector import _request
    from tradingagents.data import incremental_inputs
    from tradingagents.data.source_observations import make_observation
    from tradingagents.domain.collection import CollectionDiagnostic, CollectionDomainResult
    from tradingagents.domain.data import ProvenanceRecord

    request = _request(enabled_domains=("news",))
    retrieved = datetime(2026, 7, 24, 10, tzinfo=UTC)

    def panel(*_, data_context):
        observation = make_observation(
            "FRED", "macro_indicator", "rate", {"value": 4}, retrieved_at=retrieved
        )
        return DataResult(
            "panel",
            observations=(observation,),
            provenance=(
                ProvenanceRecord("panel", "FRED", timing="partial coverage; 1/2 cells available"),
                ProvenanceRecord("panel", "ECB", timing="retrieval unavailable"),
            ),
        )

    monkeypatch.setattr(incremental_inputs, "get_global_macro_panel", panel)
    empty = CollectionDomainResult(
        domain="news", state="unavailable", diagnostic=CollectionDiagnostic(code="test")
    )
    domain, candidates = incremental_inputs.append_news_context(
        request,
        empty,
        lambda *_a, **_k: DataResult("No news found"),
        data_context=request_context(),
    )
    sources = {s.source: s for s in domain.sources}
    assert sources["fred"].retrieved_at == retrieved
    assert sources["fred"].diagnostic.code == "upstream_source_partial"
    assert sources["ecb"].diagnostic.code == "upstream_source_unavailable"
    assert len(candidates) == 1


def test_optional_input_failures_remain_visible_without_erasing_success(monkeypatch):
    from types import SimpleNamespace

    from tests.dataflows.test_incremental_us_collector import _request
    from tradingagents.data import incremental_inputs
    from tradingagents.domain.collection import CollectionDiagnostic, CollectionDomainResult
    from tradingagents.domain.data import SourceObservation

    request = _request()
    retrieved = datetime(2026, 7, 24, 10, tzinfo=UTC)
    observation = SourceObservation(
        "yfinance", "financial_income", "NVDA", {"income": 1}, retrieved
    )

    def route(method, *_a, **_k):
        if method == "get_income_statement":
            return DataResult("statement", observations=(observation,))
        raise RuntimeError("private upstream error")

    empty = CollectionDomainResult(
        domain="fundamentals", state="unavailable", diagnostic=CollectionDiagnostic(code="test")
    )
    financial, candidates = incremental_inputs.append_financials(
        request, empty, route, data_context=request_context()
    )
    assert candidates and financial.diagnostic.code == "financial_inputs_partial"
    social, candidates = incremental_inputs.collect_professional_signals(
        request,
        lambda *_: [
            SimpleNamespace(result=DataResult("ok", observations=(observation,))),
            SimpleNamespace(result=DataResult("<source unavailable: RuntimeError>")),
        ],
    )
    assert candidates and social.diagnostic.code == "professional_signals_partial"
    market = empty.model_copy(update={"domain": "market"})
    failed, _ = incremental_inputs.append_market_context(request, market, None, route)
    assert failed.diagnostic.code == "market_snapshot_unavailable"
    assert "private" not in str((financial, social, failed))


def test_structured_news_keeps_source_limits_through_three_market_admission(monkeypatch):
    from tests.dataflows import (
        test_incremental_cn_collector as cn,
        test_incremental_jp_collector as jp,
        test_incremental_us_collector as us,
    )
    from tradingagents.data import incremental_inputs
    from tradingagents.domain.data import ProvenanceRecord
    from tradingagents.research.incremental.collection import normalize_incremental_collection

    monkeypatch.setattr(
        incremental_inputs, "get_global_macro_panel", lambda *_, data_context: DataResult("")
    )
    monkeypatch.setattr(incremental_inputs, "get_market_investor_flows", lambda *_: DataResult(""))
    retrieved = datetime(2026, 7, 24, 10, tzinfo=UTC)
    from tradingagents.data.source_observations import make_observation

    def route(method, *_a, **_k):
        if method != "get_news":
            return DataResult("No news found")
        observation = make_observation(
            "official",
            "news_article",
            "one",
            {"title": "Event"},
            available_at=retrieved,
            retrieved_at=retrieved,
        )
        return DataResult("article", observations=(observation,)).with_provenance(
            ProvenanceRecord(
                "news", "official", timing="publication-date filtered; source_window_limited"
            ),
            ProvenanceRecord("news", "media", timing="unavailable"),
        )

    for module, collector in (
        (us, us.collect_us_incremental),
        (jp, jp.collect_japan_incremental),
        (cn, cn.collect_mainland_china_incremental),
    ):
        request = module._request(enabled_domains=("news",))
        result = collector(
            request, route_to_vendor=route, now=lambda: retrieved, data_context=request_context()
        )
        summary, items, _ = normalize_incremental_collection(request, result, sealed_at=retrieved)
        sources = {s.source: s for s in summary.domains[0].sources}
        assert sources["official"].retrieved_at == retrieved
        assert sources["official"].diagnostic.code == "source_window_limited"
        assert sources["media"].diagnostic.code == "upstream_source_unavailable"
        assert len(items) == 1
