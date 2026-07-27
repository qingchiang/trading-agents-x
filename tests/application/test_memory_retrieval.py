from __future__ import annotations

from datetime import date, datetime

import pytest

from tradingagents.application.contracts import (
    AnalysisRequest,
    ResearchDecision,
    ResearchRating,
)
from tradingagents.application.repository import RunRepository


def _seed_memory(
    repository: RunRepository,
    *,
    content_hash: str,
    ticker: str,
    analysis_date: date,
    reflection: str,
    thesis: str,
    resolved: bool = True,
) -> None:
    request = AnalysisRequest(
        ticker=ticker,
        analysis_date=analysis_date,
        analysts=("market",),
    )
    decision = ResearchDecision(
        rating=ResearchRating.HOLD,
        confidence=0.5,
        thesis=thesis,
        evidence_refs=(),
        catalysts=(),
        risks=(),
        invalidation_conditions=(),
        time_horizon="Fixture horizon",
    )
    repository.import_legacy_memory(
        source_path="/fixture/memory.md",
        content_hash=content_hash,
        request=request,
        decision=decision,
        benchmark="SPY",
        raw_return=0.01 if resolved else None,
        alpha_return=0.005 if resolved else None,
        holding_intervals=5,
        observation_start=date(2026, 7, 1) if resolved else None,
        observation_end=date(2026, 7, 8) if resolved else None,
        reflection=reflection,
    )


def test_memory_context_uses_deterministic_same_and_cross_ticker_limits(
    repository: RunRepository,
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 7, 24, 12, 0, 0)
    monkeypatch.setattr(
        "tradingagents.application.repository._utc_naive",
        lambda: fixed_now,
    )
    for index in range(1, 7):
        _seed_memory(
            repository,
            content_hash=f"nvda-{index}",
            ticker="NVDA",
            analysis_date=date(2026, 6, index),
            reflection=f"same-reflection-{index}",
            thesis=f"same-decision-{index}",
        )
    for index in range(1, 5):
        _seed_memory(
            repository,
            content_hash=f"aapl-{index}",
            ticker="AAPL",
            analysis_date=date(2026, 5, index),
            reflection=f"cross-reflection-{index}",
            thesis=f"cross-decision-{index}",
        )
    _seed_memory(
        repository,
        content_hash="jp-1",
        ticker="7203.T",
        analysis_date=date(2026, 5, 1),
        reflection="japan-reflection",
        thesis="japan-decision",
    )
    _seed_memory(
        repository,
        content_hash="crypto-1",
        ticker="BTC-USD",
        analysis_date=date(2026, 5, 1),
        reflection="crypto-reflection",
        thesis="crypto-decision",
    )
    _seed_memory(
        repository,
        content_hash="pending-1",
        ticker="MSFT",
        analysis_date=date(2026, 5, 1),
        reflection="pending-reflection",
        thesis="pending-decision",
        resolved=False,
    )

    context = repository.memory_context("NVDA", "stock")

    assert "same-reflection-1" not in context
    for index in range(2, 7):
        assert f"same-reflection-{index}" in context
        assert f"same-decision-{index}" in context
    assert "cross-reflection-1" not in context
    for index in range(2, 5):
        assert f"cross-reflection-{index}" in context
        assert f"cross-decision-{index}" not in context
    assert "japan-reflection" not in context
    assert "crypto-reflection" not in context
    assert "pending-reflection" not in context
    assert context.index("same-reflection-6") < context.index("same-reflection-2")
    assert context.index("cross-reflection-4") < context.index(
        "cross-reflection-2"
    )


def test_china_cross_ticker_memory_shares_market_without_crossing_regions(
    repository: RunRepository,
) -> None:
    _seed_memory(
        repository,
        content_hash="shanghai",
        ticker="600519.SS",
        analysis_date=date(2026, 7, 1),
        reflection="Shanghai lesson",
        thesis="Shanghai decision",
    )
    _seed_memory(
        repository,
        content_hash="shenzhen",
        ticker="000001.SZ",
        analysis_date=date(2026, 7, 2),
        reflection="Shenzhen lesson",
        thesis="Shenzhen decision",
    )
    _seed_memory(
        repository,
        content_hash="tokyo",
        ticker="7203.T",
        analysis_date=date(2026, 7, 3),
        reflection="Tokyo lesson",
        thesis="Tokyo decision",
    )

    context = repository.memory_context("600000.SS", "stock")

    assert "Shanghai lesson" in context
    assert "Shenzhen lesson" in context
    assert "Tokyo lesson" not in context
    assert "Shanghai decision" not in context
    assert "Shenzhen decision" not in context


@pytest.mark.parametrize(
    ("ticker", "asset_type", "expected"),
    (
        ("NVDA", "stock", "America/New_York"),
        ("SPY", "stock", "America/New_York"),
        ("7203.T", "stock", "Asia/Tokyo"),
        ("600519.SS", "stock", "Asia/Shanghai"),
        ("000001.SZ", "stock", "Asia/Shanghai"),
        ("BTC-USD", "crypto", "CRYPTO"),
        ("ETH-USD", "crypto", "CRYPTO"),
    ),
)
def test_memory_market_bucket(
    ticker,
    asset_type,
    expected,
) -> None:
    assert RunRepository.market_bucket(ticker, asset_type) == expected


@pytest.mark.parametrize(
    ("same_limit", "cross_limit", "same_present", "cross_present"),
    (
        (0, 0, False, False),
        (1, 0, True, False),
        (0, 1, False, True),
        (1, 1, True, True),
    ),
)
def test_memory_limits_can_disable_each_context_class(
    repository: RunRepository,
    same_limit,
    cross_limit,
    same_present,
    cross_present,
) -> None:
    _seed_memory(
        repository,
        content_hash="same",
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="same lesson",
        thesis="same decision",
    )
    _seed_memory(
        repository,
        content_hash="cross",
        ticker="AAPL",
        analysis_date=date(2026, 7, 2),
        reflection="cross lesson",
        thesis="cross decision",
    )

    context = repository.memory_context(
        "NVDA",
        "stock",
        same_limit=same_limit,
        cross_limit=cross_limit,
    )

    assert ("same lesson" in context) is same_present
    assert ("cross lesson" in context) is cross_present
