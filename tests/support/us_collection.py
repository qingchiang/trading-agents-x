"""Shared offline fixtures for us collection contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tradingagents.domain.collection import IncrementalCollectionRequest


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
