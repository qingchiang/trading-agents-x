from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select

from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    ResearchRating,
    RunProfile,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    OutcomeRecord,
    ReflectionRecord,
)
from tradingagents.application.repository import RunRepository


def _seed_memory(
    repository: RunRepository,
    *,
    ticker: str,
    analysis_date: date,
    reflection: str,
    thesis: str,
    resolved: bool = True,
    rating: ResearchRating = ResearchRating.HOLD,
    catalysts: tuple[str, ...] = (),
    risks: tuple[str, ...] = (),
    invalidation_conditions: tuple[str, ...] = (),
    time_horizon: str = "Fixture horizon",
) -> str:
    request = AnalysisRequest(
        ticker=ticker,
        analysis_date=analysis_date,
        analysts=("market",),
    )
    decision = research_decision(
        rating=rating,
        confidence=0.5,
        thesis=thesis,
        evidence_refs=(),
        catalysts=catalysts,
        risks=risks or ("Legacy fixture risks were not recorded.",),
        invalidation_conditions=(
            invalidation_conditions
            or ("Legacy fixture invalidation was not recorded.",)
        ),
        time_horizon=time_horizon,
    )
    decision = decision.model_copy(
        update={
            "scenarios": tuple(
                scenario.model_copy(
                    update={"outcome": f"{ticker} {scenario.outcome}"}
                )
                for scenario in decision.scenarios
            )
        }
    )
    run, _ = repository.create_run(request, {"fixture": True})
    repository.claim_run(run.id, "fixture-worker", 30)
    evidence = EvidenceBundle(
        instrument=request.ticker,
        analysis_date=request.analysis_date,
        items=(),
    )
    repository.seal_evidence(run.id, evidence)
    repository.complete(
        run.id,
        AnalysisResult(
            run_id=run.id,
            status=RunStatus.SUCCEEDED,
            instrument=request.ticker,
            reports={},
            decision=decision,
            evidence=evidence,
        ),
        evidence=evidence,
        benchmark="SPY",
    )
    if resolved:
        with repository.sessions() as session:
            outcome_id = session.scalar(
                select(OutcomeRecord.id)
                .join(DecisionRecord)
                .where(DecisionRecord.run_id == run.id)
            )
        assert outcome_id is not None
        repository.resolve_outcome(
            outcome_id,
            observation_start=date(2026, 7, 1),
            observation_end=date(2026, 7, 8),
            raw_return=0.01,
            alpha_return=0.005,
            reflection=reflection,
        )
    return run.id


def test_memory_context_uses_deterministic_same_and_cross_ticker_limits(
    repository: RunRepository,
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 7, 24, 12, 0, 0)
    monkeypatch.setattr(
        "tradingagents.application.repository._utc_naive",
        lambda: fixed_now,
    )
    same_run_ids = []
    for index in range(1, 7):
        same_run_ids.append(
            _seed_memory(
                repository,
                ticker="NVDA",
                analysis_date=date(2026, 6, index),
                reflection=f"same-reflection-{index}",
                thesis=f"same-decision-{index}",
            )
        )
    cross_run_ids = []
    for index in range(1, 5):
        cross_run_ids.append(
            _seed_memory(
                repository,
                ticker="AAPL",
                analysis_date=date(2026, 5, index),
                reflection=f"cross-reflection-{index}",
                thesis=f"cross-decision-{index}",
            )
        )
    _seed_memory(
        repository,
        ticker="7203.T",
        analysis_date=date(2026, 5, 1),
        reflection="japan-reflection",
        thesis="japan-decision",
    )
    _seed_memory(
        repository,
        ticker="MSFT",
        analysis_date=date(2026, 5, 1),
        reflection="pending-reflection",
        thesis="pending-decision",
        resolved=False,
    )

    context = repository.memory_context("NVDA", "stock")

    same = [item for item in context.items if item.scope == "same_ticker"]
    cross = [item for item in context.items if item.scope == "same_market"]
    assert [item.reflection for item in same] == [
        f"same-reflection-{index}" for index in range(6, 1, -1)
    ]
    assert [item.decision.thesis for item in same if item.decision] == [
        f"same-decision-{index}" for index in range(6, 1, -1)
    ]
    assert [item.reflection for item in cross] == [
        f"cross-reflection-{index}" for index in range(4, 1, -1)
    ]
    assert all(item.decision is None and item.outcome is None for item in cross)
    assert context.refs == tuple(
        f"memory:{run_id}"
        for run_id in (
            *reversed(same_run_ids[1:]),
            *reversed(cross_run_ids[1:]),
        )
    )
    prompt = context.prompt_text()
    assert "same-reflection-1" not in prompt
    assert "cross-decision-4" not in prompt
    assert "japan-reflection" not in prompt
    assert "pending-reflection" not in prompt


def test_china_cross_ticker_memory_shares_market_without_crossing_regions(
    repository: RunRepository,
) -> None:
    _seed_memory(
        repository,
        ticker="600519.SS",
        analysis_date=date(2026, 7, 1),
        reflection="Shanghai lesson",
        thesis="Shanghai decision",
    )
    _seed_memory(
        repository,
        ticker="000651.SZ",
        analysis_date=date(2026, 7, 2),
        reflection="Shenzhen lesson",
        thesis="Shenzhen decision",
    )
    _seed_memory(
        repository,
        ticker="7203.T",
        analysis_date=date(2026, 7, 3),
        reflection="Tokyo lesson",
        thesis="Tokyo decision",
    )

    context = repository.memory_context("600000.SS", "stock")

    assert {item.reflection for item in context.items} == {
        "Shanghai lesson",
        "Shenzhen lesson",
    }
    assert all(item.scope == "same_market" for item in context.items)
    assert all(item.decision is None and item.outcome is None for item in context.items)


@pytest.mark.parametrize(
    ("ticker", "asset_type", "expected"),
    (
        ("NVDA", "stock", "America/New_York"),
        ("SPY", "stock", "America/New_York"),
        ("7203.T", "stock", "Asia/Tokyo"),
        ("600519.SS", "stock", "Asia/Shanghai"),
        ("000001.SZ", "stock", "Asia/Shanghai"),
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
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="same lesson",
        thesis="same decision",
    )
    _seed_memory(
        repository,
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

    reflections = {item.reflection for item in context.items}
    assert ("same lesson" in reflections) is same_present
    assert ("cross lesson" in reflections) is cross_present


def test_memory_prompt_is_bounded_per_item_and_in_total(
    repository: RunRepository,
) -> None:
    for index in range(8):
        _seed_memory(
            repository,
            ticker="NVDA" if index < 5 else "AAPL",
            analysis_date=date(2026, 7, index + 1),
            reflection=f"reflection-{index}-" + ("x" * 5_000),
            thesis=f"thesis-{index}-" + ("y" * 5_000),
        )

    context = repository.memory_context("NVDA", "stock")
    prompt = context.prompt_text()

    assert len(context.items) == 8
    assert len(prompt) <= 12_000
    assert all(len(item.prompt_text()) <= 2_000 for item in context.items)


def test_memory_context_skips_malformed_decisions_and_empty_reflections(
    repository: RunRepository,
) -> None:
    malformed_run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="Should be excluded with its malformed decision.",
        thesis="Original valid decision.",
    )
    empty_run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 2),
        reflection="Will become empty.",
        thesis="Still valid.",
    )
    with repository.sessions.begin() as session:
        malformed = session.scalar(
            select(DecisionRecord).where(DecisionRecord.run_id == malformed_run_id)
        )
        empty = session.scalar(
            select(ReflectionRecord)
            .join(
                OutcomeRecord,
                OutcomeRecord.id == ReflectionRecord.outcome_id,
            )
            .join(
                DecisionRecord,
                DecisionRecord.id == OutcomeRecord.decision_id,
            )
            .where(DecisionRecord.run_id == empty_run_id)
        )
        assert malformed is not None
        malformed.decision_json = {"rating": "not-a-rating"}
        assert empty is not None
        empty.text = "   "

    assert repository.memory_context("NVDA", "stock").items == ()


def test_memory_entries_support_fuzzy_filters_and_full_field_search(
    repository: RunRepository,
) -> None:
    nvda_run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="Valuation lesson: demand quality mattered.",
        thesis="Data center demand is accelerating.",
        rating=ResearchRating.OVERWEIGHT,
        catalysts=("Next-generation accelerator launch",),
        risks=("Power supply constraints",),
        invalidation_conditions=("Backlog contracts materially",),
        time_horizon="Three-year compound horizon",
    )
    repository.set_instrument_name(nvda_run_id, "NVIDIA")
    repository.set_instrument_local_name(nvda_run_id, "英伟达")
    _seed_memory(
        repository,
        ticker="AAPL",
        analysis_date=date(2026, 7, 2),
        reflection="Margin durability was underestimated.",
        thesis="Services growth supports margins.",
    )
    _seed_memory(
        repository,
        ticker="7203.T",
        analysis_date=date(2026, 7, 3),
        reflection="Japan-specific currency lesson.",
        thesis="Hybrid demand remains resilient.",
    )
    _seed_memory(
        repository,
        ticker="MSFT",
        analysis_date=date(2026, 7, 4),
        reflection="Pending cloud lesson.",
        thesis="Cloud growth needs confirmation.",
        resolved=False,
    )

    assert [
        entry["ticker"] for entry in repository.memory_entries(ticker="vd")
    ] == ["NVDA"]
    assert {
        entry["ticker"]
        for entry in repository.memory_entries(market="america/new")
    } == {"NVDA", "AAPL", "MSFT"}
    assert [
        entry["ticker"]
        for entry in repository.memory_entries(q="DATA CENTER")
    ] == ["NVDA"]
    by_name = repository.memory_entries(q="nvidia")
    assert [entry["ticker"] for entry in by_name] == ["NVDA"]
    assert by_name[0]["instrument_name"] == "NVIDIA"
    assert by_name[0]["instrument_local_name"] == "英伟达"
    assert by_name[0]["profile"] is RunProfile.STANDARD
    run_page = repository.list_runs(q="英伟达")
    assert run_page.items[0].research_rating is ResearchRating.OVERWEIGHT
    assert [
        entry["ticker"]
        for entry in repository.memory_entries(q="valuation LESSON")
    ] == ["NVDA"]
    for decision_query in (
        "overweight",
        "accelerator launch",
        "power supply",
        "backlog contracts",
        "three-year compound",
        "nvda fixture bull scenario outcome",
    ):
        assert [
            entry["ticker"]
            for entry in repository.memory_entries(q=decision_query)
        ] == ["NVDA"]
    assert [
        entry["ticker"]
        for entry in repository.memory_entries(q=nvda_run_id[:12])
    ] == ["NVDA"]
    assert [
        entry["ticker"]
        for entry in repository.memory_entries(q="asia/tokyo")
    ] == ["7203.T"]
    assert [
        entry["ticker"]
        for entry in repository.memory_entries(status="pending")
    ] == ["MSFT"]
    assert repository.memory_entries(q="pending cloud", status="resolved") == []
    assert repository.memory_entries(q="%") == []


def test_retained_crypto_is_excluded_from_active_memory(
    repository: RunRepository,
) -> None:
    run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="Legacy Crypto reflection must remain read-only.",
        thesis="Legacy fixture.",
    )
    with repository.sessions.begin() as session:
        decision = session.scalar(
            select(DecisionRecord).where(DecisionRecord.run_id == run_id)
        )
        decision.asset_type = "crypto"

    assert repository.memory_context("NVDA", "stock").items == ()
    assert repository.memory_entries() == []
