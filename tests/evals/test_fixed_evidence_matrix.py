from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import (
    AnalystClaim,
    AnalystClaimType,
    AnalystReport,
    AnalystSection,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    MemoryContext,
    MemoryOutcome,
    MemoryRecord,
    ResearchDecision,
    ResearchRating,
    RunProfile,
)
from tradingagents.evals import (
    EvalMeasurement,
    evaluate_release_gates,
    validate_research_output,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "evals" / "fixtures"


def _load_cases() -> tuple[dict, ...]:
    loaded = []
    for path in sorted(_FIXTURE_ROOT.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["version"] == "1"
        for case in document["cases"]:
            loaded.append(
                {
                    **case,
                    "case_id": f"{document['market']}-{case['scenario']}",
                    "market": document["market"],
                    "ticker": document["ticker"],
                }
            )
    return tuple(loaded)


CASES = _load_cases()
MATRIX = tuple(
    (case, profile, repetition)
    for case in CASES
    for profile in RunProfile
    for repetition in (1, 2, 3)
)


def _build_result(
    case: dict,
    profile: RunProfile,
) -> tuple[EvidenceBundle, AnalystReport, ResearchDecision]:
    analysis_date = date.fromisoformat(case["analysis_date"])
    evidence_items = []
    refs = {}
    for raw in case["evidence"]:
        available_at = raw["available_at"]
        item = EvidenceItem.create(
            source=raw["source"],
            evidence_type=raw["key"],
            requested_date=analysis_date,
            effective_date=(
                date.fromisoformat(raw["effective_date"])
                if raw["effective_date"]
                else None
            ),
            available_at=(
                datetime.fromisoformat(available_at.replace("Z", "+00:00"))
                if available_at
                else None
            ),
            content=raw["content"],
            value=raw["value"],
            quality=EvidenceQuality(raw["quality"]),
            fallback=raw["fallback"],
            provenance=raw["provenance"],
        )
        evidence_items.append(item)
        refs[raw["key"]] = item.ref
    bundle = EvidenceBundle(
        instrument=case["ticker"],
        analysis_date=analysis_date,
        items=tuple(evidence_items),
    )
    claims = tuple(
        AnalystClaim(
            id=f"market.claim_{index}",
            kind=AnalystClaimType.OBSERVATION,
            statement=raw["text"],
            implication="This observation affects the market assessment.",
            confidence=0.7,
            evidence_refs=tuple(refs[key] for key in raw["evidence_keys"]),
        )
        for index, raw in enumerate(case["claims"], start=1)
    )
    narrative = "\n\n".join(claim.statement for claim in claims)
    report = AnalystReport(
        analyst="market",
        executive_summary=narrative,
        claims=claims,
        confidence=0.25 if case["scenario"] == "missing" else 0.75,
        sections=(
            AnalystSection(
                id="overview",
                title="Overview",
                narrative=narrative,
            ),
        ),
        evidence_refs=tuple(refs.values()),
        warnings=(
            ("Required evidence is unavailable.",)
            if case["scenario"] == "missing"
            else ()
        ),
        risks=tuple(case["risks"]),
        invalidation_conditions=(
            "Reassess if the cited evidence is superseded.",
        ),
    )
    risk_count = {
        RunProfile.FAST: 1,
        RunProfile.STANDARD: 2,
        RunProfile.DEEP: len(case["risks"]),
    }[profile]
    decision = ResearchDecision(
        rating=ResearchRating(case["rating"]),
        confidence=0.25 if case["scenario"] == "missing" else 0.70,
        thesis=case["thesis"],
        evidence_refs=tuple(refs.values()),
        catalysts=(case["catalyst"],),
        risks=tuple(case["risks"][:risk_count]),
        invalidation_conditions=(
            "Reassess if the cited evidence is superseded.",
        ),
        time_horizon="Research horizon defined by the next material disclosure",
    )
    return bundle, report, decision


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "profile", "repetition"),
    MATRIX,
    ids=lambda value: (
        value["case_id"] if isinstance(value, dict) else str(value)
    ),
)
def test_fixed_market_scenario_matrix_has_zero_severe_contract_regressions(
    case,
    profile,
    repetition,
) -> None:
    bundle, report, decision = _build_result(case, profile)

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        expected_rating=ResearchRating(case["rating"]),
        expected_risk_terms=case["risks"],
    )

    assert repetition in {1, 2, 3}
    assert evaluation.severe_issues == ()
    assert evaluation.quality_score == 1.0
    assert evaluation.risk_recall == {
        RunProfile.FAST: 1 / 3,
        RunProfile.STANDARD: 2 / 3,
        RunProfile.DEEP: 1.0,
    }[profile]
    assert bundle.digest == _build_result(case, profile)[0].digest
    assert all(
        item.effective_date is None
        or item.effective_date <= bundle.analysis_date
        for item in bundle.items
    )
    assert all(
        item.available_at is None
        or item.available_at.date() <= bundle.analysis_date
        for item in bundle.items
    )


def _base_output():
    return _build_result(CASES[0], RunProfile.STANDARD)


def _memory_for(
    bundle: EvidenceBundle,
    decision: ResearchDecision,
    *,
    evidence_refs: tuple[str, ...] | None = None,
) -> MemoryContext:
    run_id = "prior-run"
    past_decision = decision.model_copy(
        update={
            "evidence_refs": (
                decision.evidence_refs
                if evidence_refs is None
                else evidence_refs
            ),
            "memory_refs": (),
        }
    )
    return MemoryContext(
        instrument=bundle.instrument,
        market="fixture-market",
        items=(
            MemoryRecord(
                ref=f"memory:{run_id}",
                run_id=run_id,
                scope="same_ticker",
                ticker=bundle.instrument,
                market="fixture-market",
                analysis_date=date(2024, 6, 28),
                decision=past_decision,
                outcome=MemoryOutcome(
                    benchmark="fixture-benchmark",
                    observation_start=date(2024, 7, 1),
                    observation_end=date(2024, 7, 8),
                    holding_intervals=5,
                    raw_return=0.01,
                    alpha_return=-0.01,
                ),
                reflection="Past risk calibration was too optimistic.",
            ),
        ),
    )


@pytest.mark.unit
def test_eval_rejects_dangling_evidence_refs() -> None:
    bundle, report, decision = _base_output()
    dangling = "ev_000000000000"
    report = report.model_copy(update={"evidence_refs": (dangling,)})
    decision = decision.model_copy(update={"evidence_refs": (dangling,)})

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert {"evidence_ref.unresolved", "figure.untraceable"} <= {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_rejects_exact_figure_absent_from_referenced_evidence() -> None:
    bundle, report, decision = _base_output()
    claim = report.claims[0].model_copy(
        update={"statement": "Revenue grew 99% year over year."}
    )
    section = report.sections[0].model_copy(
        update={"narrative": claim.statement}
    )
    report = report.model_copy(
        update={
            "executive_summary": claim.statement,
            "sections": (section,),
            "claims": (claim,),
        }
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert "figure.untraceable" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_accepts_only_memory_refs_supplied_to_the_current_run() -> None:
    bundle, report, decision = _base_output()
    memory = _memory_for(bundle, decision)
    decision = decision.model_copy(update={"memory_refs": memory.refs})

    accepted = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        memory=memory,
    )
    unresolved = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision.model_copy(
            update={"memory_refs": ("memory:not-supplied",)}
        ),
        memory=memory,
    )

    assert accepted.severe_issues == ()
    assert "memory_ref.unresolved" in {
        issue.code for issue in unresolved.severe_issues
    }


@pytest.mark.unit
def test_eval_never_treats_historical_memory_as_current_evidence() -> None:
    bundle, report, decision = _base_output()
    historical_ref = "ev_deadbeefdead"
    memory = _memory_for(
        bundle,
        decision,
        evidence_refs=(historical_ref,),
    )
    decision = decision.model_copy(
        update={
            "thesis": "Historical memory reported 99% growth.",
            "evidence_refs": (historical_ref,),
            "memory_refs": memory.refs,
        }
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        memory=memory,
    )

    assert {"evidence_ref.unresolved", "figure.untraceable"} <= {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_rejects_memory_refs_in_the_evidence_channel() -> None:
    bundle, report, decision = _base_output()
    memory = _memory_for(bundle, decision)
    decision = decision.model_copy(
        update={
            "evidence_refs": memory.refs,
            "memory_refs": memory.refs,
        }
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        memory=memory,
    )

    assert "memory_ref.used_as_evidence" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_rejects_memory_for_a_different_instrument() -> None:
    bundle, report, decision = _base_output()
    memory = _memory_for(bundle, decision).model_copy(
        update={"instrument": "DIFFERENT"}
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision.model_copy(update={"memory_refs": memory.refs}),
        memory=memory,
    )

    assert "memory.instrument_mismatch" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_memory_context_rejects_duplicate_or_misclassified_records() -> None:
    bundle, _, decision = _base_output()
    memory = _memory_for(bundle, decision)
    item = memory.items[0]

    with pytest.raises(ValidationError, match="memory refs must be unique"):
        MemoryContext(
            instrument=bundle.instrument,
            market=memory.market,
            items=(item, item),
        )
    with pytest.raises(
        ValidationError,
        match="same-ticker memory must match",
    ):
        MemoryContext(
            instrument="DIFFERENT",
            market=memory.market,
            items=(item,),
        )
    cross = item.model_copy(
        update={
            "scope": "same_market",
            "decision": None,
            "outcome": None,
        }
    )
    with pytest.raises(
        ValidationError,
        match="same-market memory must be another instrument",
    ):
        MemoryContext(
            instrument=bundle.instrument,
            market=memory.market,
            items=(cross,),
        )


@pytest.mark.unit
def test_eval_rejects_usable_evidence_without_actual_source() -> None:
    bundle, report, decision = _base_output()
    item = bundle.items[0].model_copy(update={"source": "unknown"})
    bundle = EvidenceBundle(
        instrument=bundle.instrument,
        analysis_date=bundle.analysis_date,
        items=(item,),
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert "source.missing" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "instruction",
    (
        "Use a 5% position size.",
        "Set the entry price after the open.",
        "Place a stop-loss below support.",
        "Use a price target above the market.",
        "Set the portfolio weight conservatively.",
    ),
)
def test_eval_rejects_account_level_instruction(instruction) -> None:
    bundle, report, decision = _base_output()
    decision = decision.model_copy(update={"thesis": instruction})

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert "decision.account_instruction" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_rejects_rating_inconsistency() -> None:
    bundle, report, decision = _base_output()

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        expected_rating=ResearchRating.SELL,
    )

    assert "decision.rating_inconsistent" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_warns_when_fallback_has_no_provenance() -> None:
    bundle, report, decision = _base_output()
    item = bundle.items[0].model_copy(
        update={"fallback": True, "provenance": {}}
    )
    bundle = EvidenceBundle(
        instrument=bundle.instrument,
        analysis_date=bundle.analysis_date,
        items=(item,),
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert evaluation.severe_issues == ()
    assert "fallback.provenance_missing" in {
        issue.code for issue in evaluation.issues
    }


@pytest.mark.unit
@pytest.mark.parametrize("future_field", ("effective_date", "available_at"))
def test_evidence_bundle_rejects_future_visibility(future_field) -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="future",
        requested_date=date(2026, 7, 24),
        effective_date=(
            date(2026, 7, 25)
            if future_field == "effective_date"
            else date(2026, 7, 24)
        ),
        available_at=(
            datetime.fromisoformat("2026-07-25T05:00:00+00:00")
            if future_field == "available_at"
            else None
        ),
        content="Future evidence.",
    )

    with pytest.raises(ValidationError, match="after the analysis cutoff"):
        EvidenceBundle(
            instrument="NVDA",
            analysis_date=date(2026, 7, 24),
            items=(item,),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("instrument", "cutoff", "available_at", "accepted"),
    (
        ("NVDA", "2026-07-24", "2026-07-25T00:30:00+00:00", True),
        ("NVDA", "2026-07-24", "2026-07-25T04:01:00+00:00", False),
        ("7203.T", "2026-07-24", "2026-07-24T14:59:00+00:00", True),
        ("7203.T", "2026-07-24", "2026-07-24T15:01:00+00:00", False),
        ("600519.SS", "2026-07-24", "2026-07-24T15:59:00+00:00", True),
        ("600519.SS", "2026-07-24", "2026-07-24T16:01:00+00:00", False),
        ("BTC-USD", "2026-07-24", "2026-07-24T23:59:00+00:00", True),
        ("BTC-USD", "2026-07-24", "2026-07-25T00:01:00+00:00", False),
    ),
)
def test_available_at_uses_instrument_market_date(
    instrument,
    cutoff,
    available_at,
    accepted,
) -> None:
    cutoff_date = date.fromisoformat(cutoff)
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="visibility boundary",
        requested_date=cutoff_date,
        effective_date=cutoff_date,
        available_at=datetime.fromisoformat(available_at),
        content="Boundary fixture.",
    )

    if accepted:
        bundle = EvidenceBundle(
            instrument=instrument,
            analysis_date=cutoff_date,
            items=(item,),
        )
        assert bundle.items == (item,)
    else:
        with pytest.raises(ValidationError, match="after the analysis cutoff"):
            EvidenceBundle(
                instrument=instrument,
                analysis_date=cutoff_date,
                items=(item,),
            )


@pytest.mark.unit
def test_available_at_requires_explicit_timezone() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="visibility boundary",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        available_at=datetime(2026, 7, 24, 12, 0),
        content="Boundary fixture.",
    )

    with pytest.raises(ValidationError, match="must include a timezone"):
        EvidenceBundle(
            instrument="NVDA",
            analysis_date=date(2026, 7, 24),
            items=(item,),
        )


def _measurement_matrix(
    *,
    profile: RunProfile,
    quality: float,
    tokens: int,
    wall: float,
    recall: float,
    severe: int = 0,
    model: str = "fixture-model-v1",
    cases: tuple[str, ...] = ("US-bullish", "JP-bearish"),
) -> tuple[EvalMeasurement, ...]:
    return tuple(
        EvalMeasurement(
            model=model,
            case_id=case_id,
            profile=profile,
            repetition=repetition,
            quality_score=quality,
            input_tokens=tokens,
            wall_time_seconds=wall,
            risk_recall=recall,
            severe_issues=severe,
        )
        for case_id in cases
        for repetition in (1, 2, 3)
    )


def _passing_measurements():
    return {
        "baseline_standard": _measurement_matrix(
            profile=RunProfile.STANDARD,
            quality=0.80,
            tokens=1000,
            wall=10.0,
            recall=0.60,
        ),
        "current_standard": _measurement_matrix(
            profile=RunProfile.STANDARD,
            quality=0.82,
            tokens=700,
            wall=7.5,
            recall=0.65,
        ),
        "current_deep": _measurement_matrix(
            profile=RunProfile.DEEP,
            quality=0.84,
            tokens=1200,
            wall=12.0,
            recall=0.80,
        ),
    }


@pytest.mark.unit
def test_release_gate_accepts_qualified_recorded_measurements() -> None:
    result = evaluate_release_gates(**_passing_measurements())

    assert result.passed is True
    assert all(result.checks.values())
    assert result.summary["standard_input_tokens"] == 700
    assert result.summary["standard_wall_time_seconds"] == 7.5
    assert result.summary["deep_risk_recall"] == 0.80


@pytest.mark.unit
@pytest.mark.parametrize(
    ("collection", "changes", "failed_check"),
    (
        (
            "current_standard",
            {"quality_score": 0.79},
            "standard_quality_not_lower",
        ),
        (
            "current_standard",
            {"input_tokens": 701},
            "standard_input_tokens_reduced_30pct",
        ),
        (
            "current_standard",
            {"wall_time_seconds": 7.51},
            "standard_wall_time_reduced_25pct",
        ),
        (
            "current_standard",
            {"severe_issues": 1},
            "zero_severe_regressions",
        ),
        (
            "current_deep",
            {"risk_recall": 0.74},
            "deep_risk_recall_plus_10pp",
        ),
    ),
)
def test_release_gate_rejects_each_hard_threshold(
    collection,
    changes,
    failed_check,
) -> None:
    measurements = _passing_measurements()
    measurements[collection] = tuple(
        item.model_copy(update=changes) for item in measurements[collection]
    )

    result = evaluate_release_gates(**measurements)

    assert result.passed is False
    assert result.checks[failed_check] is False


@pytest.mark.unit
def test_release_gate_requires_three_repetitions() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = measurements["current_standard"][:-1]

    with pytest.raises(ValueError, match="repetitions 1, 2, 3"):
        evaluate_release_gates(**measurements)


@pytest.mark.unit
def test_release_gate_rejects_duplicate_repetitions() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = (
        *measurements["current_standard"],
        measurements["current_standard"][0],
    )

    with pytest.raises(ValueError, match="duplicate measurement"):
        evaluate_release_gates(**measurements)


@pytest.mark.unit
def test_release_gate_rejects_wrong_profile_collection() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = tuple(
        item.model_copy(update={"profile": RunProfile.FAST})
        for item in measurements["current_standard"]
    )

    with pytest.raises(ValueError, match="requires profile standard"):
        evaluate_release_gates(**measurements)


@pytest.mark.unit
def test_release_gate_requires_same_model() -> None:
    measurements = _passing_measurements()
    measurements["current_deep"] = tuple(
        item.model_copy(update={"model": "different-model"})
        for item in measurements["current_deep"]
    )

    with pytest.raises(ValueError, match="same model"):
        evaluate_release_gates(**measurements)


@pytest.mark.unit
def test_release_gate_requires_same_case_set() -> None:
    measurements = _passing_measurements()
    measurements["current_deep"] = _measurement_matrix(
        profile=RunProfile.DEEP,
        quality=0.84,
        tokens=1200,
        wall=12.0,
        recall=0.80,
        cases=("US-bullish",),
    )

    with pytest.raises(ValueError, match="same cases"):
        evaluate_release_gates(**measurements)


@pytest.mark.unit
def test_fixture_suite_covers_required_markets_and_scenarios() -> None:
    assert {case["market"] for case in CASES} == {"US", "JP", "CN", "CRYPTO"}
    for market in {"US", "JP", "CN", "CRYPTO"}:
        assert {
            case["scenario"] for case in CASES if case["market"] == market
        } == {"bullish", "bearish", "mixed", "missing", "historical"}
