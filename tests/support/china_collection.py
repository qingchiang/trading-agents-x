"""Shared offline fixtures for china collection contracts."""

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
            "data_vendors_by_market": {".SS": {"core_stock_apis": "akshare,yfinance"}}
        },
    )
