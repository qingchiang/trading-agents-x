"""Shared offline fixtures for japan collection contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tradingagents.domain.collection import IncrementalCollectionRequest


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
        configured_routes={
            "data_vendors_by_market": {".T": {"core_stock_apis": "jquants,yfinance"}}
        },
    )
