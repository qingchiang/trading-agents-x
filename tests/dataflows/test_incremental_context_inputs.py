from datetime import UTC, date, datetime

from tradingagents.dataflows.macro_common import SeriesCache


def test_macro_cache_preserves_original_retrieval_across_instances(tmp_path, monkeypatch):
    from tradingagents.dataflows.config import get_config, use_config

    with use_config({**get_config(), "data_cache_dir": str(tmp_path)}):
        data = {"points": [("2026-08-01", "2.5")]}
        key = ("policy", date.today().isoformat(), 30)
        first = SeriesCache(namespace="observation-test")
        first.put_observation(key, data)
        original = data["retrieved_at"]
        second = SeriesCache(namespace="observation-test")
        assert second.get(key)["retrieved_at"] == original


def test_same_structured_observation_does_not_advance_after_refresh():
    from dataclasses import replace

    from tradingagents.application.contracts import PerformanceComponent, PerformanceObservation
    from tradingagents.application.incremental_collection import assess_information_advancement
    from tradingagents.dataflows.source_observations import SourceObservation

    original = SourceObservation("FRED", "macro_indicator", "rate", {"value": 4.5},
                                 datetime(2026, 9, 4, tzinfo=UTC))
    fresh = replace(original, retrieved_at=datetime(2026, 9, 5, tzinfo=UTC))
    result = assess_information_advancement(
        baseline_items=(original.evidence(date(2026, 9, 4)),),
        current_items=(fresh.evidence(date(2026, 9, 5)),),
        performance=PerformanceObservation(stock=PerformanceComponent(status="unavailable", reason="test")),
        stock_series_admitted=False,
    )
    assert not result.advanced
