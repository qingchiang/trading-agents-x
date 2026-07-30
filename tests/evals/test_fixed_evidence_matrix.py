from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalystClaimType,
    AnalystReport,
    ClaimImportance,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    KeyClaim,
    ReportAuditStatus,
    ReportSection,
    ResearchRating,
)
from tradingagents.evals import (
    EvalMeasurement,
    QualityScores,
    evaluate_release_gates,
    validate_research_output,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "evals" / "fixtures"


def _load_cases() -> tuple[dict, ...]:
    loaded = []
    for path in sorted(_FIXTURE_ROOT.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for case in document["cases"]:
            loaded.append(
                {
                    **case,
                    "case_id": f"{document['market']}-{case['scenario']}",
                    "market": document["market"],
                }
            )
    return tuple(loaded)


CASES = _load_cases()


def _base_output():
    revenue = EvidenceItem.create(
        source="SEC 10-Q",
        evidence_type="income statement",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 18),
        available_at=datetime(2026, 7, 18, 20, tzinfo=timezone.utc),
        content="Revenue was 100 and operating cost was 80.",
        value=100,
        quality=EvidenceQuality.HIGH,
    )
    cost = EvidenceItem.create(
        source="SEC 10-Q",
        evidence_type="income statement",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 18),
        available_at=datetime(2026, 7, 18, 20, tzinfo=timezone.utc),
        content="Operating cost was 80 against revenue of 100.",
        value=80,
        quality=EvidenceQuality.HIGH,
    )
    bundle = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(revenue, cost),
    )
    refs = (revenue.ref, cost.ref)
    report = AnalystReport(
        analyst="fundamentals",
        markdown=(
            "# Overview\n\n"
            "| Metric | Value |\n|---|---:|\n| Revenue | 100 |\n| Cost | 80 |\n\n"
            f"Revenue exceeded cost.[^{revenue.ref}] [^{cost.ref}]"
        ),
        report_sections=(
            ReportSection(
                id="fundamentals.section.overview",
                title="Overview",
                anchor="overview",
                source_refs=refs,
            ),
        ),
        confidence=0.75,
        key_claims=(
            KeyClaim(
                id="fundamentals.claim_1",
                section_id="fundamentals.section.overview",
                kind=AnalystClaimType.OBSERVATION,
                importance=ClaimImportance.PRIMARY,
                statement="Revenue was 100 and operating cost was 80.",
                implication="The spread informs operating quality.",
                confidence=0.8,
                evidence_refs=refs,
            ),
        ),
        source_refs=refs,
        audit_status=ReportAuditStatus.COMPLETE,
    )
    decision = research_decision(
        evidence_refs=refs,
        thesis="The evidence supports a conditional conclusion.",
        risks=("Operating cost could increase.",),
    )
    return bundle, report, decision


@pytest.mark.unit
def test_fixture_suite_covers_required_markets_and_scenarios() -> None:
    markets = {case["market"] for case in CASES}
    scenarios = {case["scenario"] for case in CASES}

    assert {"US", "JP", "CN", "CRYPTO"} <= markets
    assert {"bullish", "bearish", "mixed", "missing", "historical"} <= scenarios


@pytest.mark.unit
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_fixed_tool_inputs_are_point_in_time(case: dict) -> None:
    cutoff = date.fromisoformat(case["analysis_date"])
    for evidence in case["evidence"]:
        if evidence["effective_date"]:
            assert date.fromisoformat(evidence["effective_date"]) <= cutoff


@pytest.mark.unit
def test_eval_accepts_markdown_report_with_small_complete_audit() -> None:
    bundle, report, decision = _base_output()

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        table_expected=True,
    )

    assert evaluation.severe_issues == ()


@pytest.mark.unit
def test_eval_accepts_explicit_incomplete_report_audit() -> None:
    bundle, report, decision = _base_output()
    incomplete = report.model_copy(
        update={
            "key_claims": (),
            "audit_status": ReportAuditStatus.INCOMPLETE,
        }
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(incomplete,),
        decision=decision,
    )

    assert not any(
        issue.code == "report.audit_incomplete"
        and issue.severity == "severe"
        for issue in evaluation.issues
    )


@pytest.mark.unit
def test_eval_rejects_dangling_decision_evidence() -> None:
    bundle, report, decision = _base_output()
    invalid = decision.model_copy(
        update={"evidence_refs": ("ev_ffffffffffff",)}
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=invalid,
    )

    assert any(
        issue.code == "evidence_ref.unresolved"
        for issue in evaluation.severe_issues
    )


@pytest.mark.unit
def test_evidence_bundle_rejects_future_visibility() -> None:
    item = EvidenceItem.create(
        source="future",
        evidence_type="filing",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 25),
        content="Future evidence.",
    )

    with pytest.raises(ValidationError):
        EvidenceBundle(
            instrument="NVDA",
            analysis_date=date(2026, 7, 24),
            items=(item,),
        )


def _measurement(
    *,
    variant: str,
    repetition: int,
    quality: float,
    risk_recall: float,
    metrics_scale: int = 1,
) -> EvalMeasurement:
    layer, profile = {
        "main_analyst": ("analyst", "analyst"),
        "v2_analyst": ("analyst", "analyst"),
        "main_medium": ("graph", "medium"),
        "v2_standard": ("graph", "standard"),
        "v2_deep": ("graph", "deep"),
    }[variant]
    commit = "1" * 40 if variant.startswith("main") else "2" * 40
    return EvalMeasurement(
        layer=layer,
        variant=variant,
        profile=profile,
        commit_sha=commit,
        provider="fixture",
        quick_model="quick",
        deep_model="deep",
        output_language="en",
        prompt_hash="a" * 64,
        runtime_prompt_hash="b" * 64,
        evidence_hash="c" * 64,
        output_hash=f"{repetition:064x}",
        artifact_path=f"{variant}-{repetition}.json",
        case_id="US-mixed",
        repetition=repetition,
        quality=QualityScores(
            factual_completeness=quality,
            analytical_depth=quality,
            table_readability=quality,
            decision_utility=quality,
        ),
        reviewer="fixture",
        rubric_version="1",
        llm_calls=10 * metrics_scale,
        input_tokens=1_000 * metrics_scale,
        output_tokens=100 * metrics_scale,
        wall_time_seconds=10 * metrics_scale,
        risk_recall=risk_recall,
        severe_issues=0,
    )


def _matrix(
    variant: str,
    quality: float,
    risk_recall: float,
    *,
    metrics_scale: int = 1,
) -> tuple[EvalMeasurement, ...]:
    return tuple(
        _measurement(
            variant=variant,
            repetition=repetition,
            quality=quality,
            risk_recall=risk_recall,
            metrics_scale=metrics_scale,
        )
        for repetition in (1, 2, 3)
    )


def _passing_measurements() -> dict[str, tuple[EvalMeasurement, ...]]:
    return {
        "baseline_analyst": _matrix("main_analyst", 0.80, 0.60),
        "current_analyst": _matrix("v2_analyst", 0.82, 0.60),
        "baseline_medium": _matrix("main_medium", 0.80, 0.60),
        "current_standard": _matrix("v2_standard", 0.82, 0.65),
        "current_deep": _matrix("v2_deep", 0.84, 0.75),
    }


@pytest.mark.unit
def test_release_gate_is_quality_first() -> None:
    result = evaluate_release_gates(**_passing_measurements())

    assert result.passed is True
    assert not any(
        "tokens_reduced" in check or "wall_time_reduced" in check
        for check in result.checks
    )


@pytest.mark.unit
def test_release_gate_does_not_use_token_or_latency_thresholds() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = _matrix(
        "v2_standard", 0.82, 0.65, metrics_scale=100
    )
    measurements["current_deep"] = _matrix(
        "v2_deep", 0.84, 0.75, metrics_scale=200
    )

    result = evaluate_release_gates(**measurements)

    assert result.passed is True
    assert result.summary["current_deep_input_tokens"] == 200_000


@pytest.mark.unit
def test_release_gate_rejects_quality_regression() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = _matrix(
        "v2_standard", 0.79, 0.65
    )

    result = evaluate_release_gates(**measurements)

    assert result.passed is False
    assert result.checks["standard_analytical_depth_not_lower"] is False


@pytest.mark.unit
def test_release_gate_requires_three_repetitions() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = measurements["current_standard"][:-1]

    with pytest.raises(ValueError, match="repetitions 1, 2, 3"):
        evaluate_release_gates(**measurements)


@pytest.mark.unit
def test_rating_consistency_remains_a_hard_gate() -> None:
    bundle, report, decision = _base_output()

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        expected_rating=ResearchRating.BUY,
    )

    assert any(
        issue.code == "decision.rating_inconsistent"
        for issue in evaluation.severe_issues
    )
