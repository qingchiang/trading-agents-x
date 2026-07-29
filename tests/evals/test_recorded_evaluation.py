from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.adapters import main_medium as main_adapter
from scripts.run_graph_evaluation import _require_live_authorization
from tests.factories import analyst_report
from tradingagents.application.contracts import (
    EvidenceBundle,
    EvidenceItem,
    RunMetrics,
)
from tradingagents.evals import (
    EvalReview,
    EvaluationRuntimeIdentity,
    PromptHashCallback,
    QualityScores,
    RecordedEvalOutput,
    build_call_plan,
    canonical_hash,
    measurement_from_record,
    prepare_contract_fixture_suite,
    prepare_quality_fixture_suite,
    validate_analyst_output,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "evals" / "fixtures"
_QUALITY_SPEC = (
    Path(__file__).resolve().parents[2]
    / "evals"
    / "quality_fixtures"
    / "research_v2.json"
)


@pytest.mark.unit
def test_prepare_contract_fixtures_builds_sealed_prompt_inputs() -> None:
    suite = prepare_contract_fixture_suite(_FIXTURE_ROOT)

    assert len(suite.cases) == 20
    assert {case.case_id.split("-", 1)[0] for case in suite.cases} == {
        "US",
        "JP",
        "CN",
        "CRYPTO",
    }
    assert all(case.evidence.digest for case in suite.cases)
    assert all(not case.reports for case in suite.cases)
    assert {
        case.analyst_inputs[0].analyst
        for case in suite.cases
        if case.case_id.startswith("CRYPTO-")
    } == {"market"}
    assert {
        case.analyst_inputs[0].analyst
        for case in suite.cases
        if not case.case_id.startswith("CRYPTO-")
    } == {"fundamentals"}


@pytest.mark.unit
def test_call_plan_reports_quality_matrix_without_recovery_calls() -> None:
    plan = build_call_plan(
        analyst_jobs=20,
        graph_cases=20,
        repetitions=3,
    )

    assert plan.primary_calls == {
        "main_analyst": 60,
        "v2_analyst": 60,
        "main_medium": 1080,
        "v2_standard": 480,
        "v2_deep_min": 600,
        "v2_deep_max": 840,
    }
    assert plan.total_primary_calls_min == 2280
    assert plan.total_primary_calls_max == 2520
    assert plan.recovery_calls_excluded is True


@pytest.mark.unit
def test_prepare_quality_suite_covers_all_markets_roles_and_scenarios() -> None:
    suite = prepare_quality_fixture_suite(_QUALITY_SPEC)

    assert len(suite.cases) == 20
    assert sum(len(case.analyst_inputs) for case in suite.cases) == 75
    assert {
        case.case_id.split("-", 1)[0] for case in suite.cases
    } == {"US", "JP", "CN", "CRYPTO"}
    for case in suite.cases:
        roles = {item.analyst for item in case.analyst_inputs}
        expected = (
            {"market", "social", "news"}
            if case.case_id.startswith("CRYPTO-")
            else {"market", "social", "news", "fundamentals"}
        )
        assert roles == expected
        assert not case.reports
        assert case.evidence.digest
        assert case.table_expected is True
        combined_refs = {item.ref for item in case.evidence.items}
        for analyst_input in case.analyst_inputs:
            assert {
                item.ref for item in analyst_input.evidence.items
            }.issubset(combined_refs)
            assert analyst_input.evidence.tables
            assert analyst_input.table_expected is True


@pytest.mark.unit
def test_quality_suite_is_point_in_time_and_contract_clean() -> None:
    suite = prepare_quality_fixture_suite(_QUALITY_SPEC)

    for case in suite.cases:
        assert all(
            item.effective_date is None
            or item.effective_date <= case.evidence.analysis_date
            for item in case.evidence.items
        )
        evaluation = validate_analyst_output(
            bundle=case.evidence,
            reports=(),
        )
        assert evaluation.severe_issues == ()


@pytest.mark.unit
def test_quality_call_plan_reports_full_paid_matrix_scale() -> None:
    suite = prepare_quality_fixture_suite(_QUALITY_SPEC)
    plan = build_call_plan(
        analyst_jobs=sum(
            len(case.analyst_inputs) for case in suite.cases
        ),
        graph_cases=len(suite.cases),
        repetitions=3,
    )

    assert plan.primary_calls == {
        "main_analyst": 225,
        "v2_analyst": 225,
        "main_medium": 1080,
        "v2_standard": 480,
        "v2_deep_min": 600,
        "v2_deep_max": 840,
    }
    assert plan.total_primary_calls_min == 2610
    assert plan.total_primary_calls_max == 2850


@pytest.mark.unit
def test_prompt_hash_is_stable_across_parallel_callback_order() -> None:
    first = PromptHashCallback()
    first.on_llm_start({}, ["alpha"], run_id="a")
    first.on_llm_start({}, ["beta"], run_id="b")
    second = PromptHashCallback()
    second.on_llm_start({}, ["beta"], run_id="b")
    second.on_llm_start({}, ["alpha"], run_id="a")

    assert first.digest() == second.digest()


@pytest.mark.unit
def test_prompt_hash_deduplicates_duplicate_callback_for_same_run() -> None:
    callback = PromptHashCallback()
    callback.on_llm_start({}, ["alpha"], run_id="same")
    callback.on_llm_start({}, ["different duplicate"], run_id="same")
    expected = PromptHashCallback()
    expected.on_llm_start({}, ["alpha"], run_id="same")

    assert callback.digest() == expected.digest()


@pytest.mark.unit
def test_blinded_review_materializes_recorded_measurement() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="recorded",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Recorded evidence.",
    )
    evidence = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    report = analyst_report(evidence_ref=item.ref)
    record = RecordedEvalOutput(
        variant="v2_analyst",
        case_id="US-bullish",
        source_case_id="US-bullish",
        repetition=1,
        runtime=EvaluationRuntimeIdentity(
            commit_sha="a" * 40,
            provider="fixture",
            quick_model="fixture-model",
            deep_model="fixture-model",
            output_language="en",
            temperature=0.0,
        ),
        prompt_hash=canonical_hash("prompt"),
        runtime_prompt_hash=canonical_hash("runtime prompt"),
        evidence_hash=evidence.digest,
        output_hash=canonical_hash(report.model_dump(mode="json")),
        metrics=RunMetrics(
            llm_calls=1,
            input_tokens=100,
            output_tokens=50,
            wall_time_seconds=2.0,
        ),
        risk_recall=1.0,
        evidence=evidence,
        reports={"market": report},
    )
    review = EvalReview(
        record_id=record.record_id,
        quality=QualityScores(
            factual_completeness=0.8,
            analytical_depth=0.9,
            table_readability=0.7,
            decision_utility=0.8,
        ),
        reviewer="blind-reviewer",
        rubric_version="quality-v1",
    )

    measurement = measurement_from_record(
        record,
        review,
        artifact_path="records.jsonl#record",
    )

    assert measurement.variant == "v2_analyst"
    assert measurement.layer == "analyst"
    assert measurement.quality.analytical_depth == 0.9
    assert measurement.input_tokens == 100


@pytest.mark.unit
def test_review_cannot_be_joined_to_different_record() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="recorded",
        requested_date=date(2026, 7, 24),
        content="Recorded evidence.",
    )
    evidence = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    record = RecordedEvalOutput(
        variant="main_analyst",
        case_id="US-bullish",
        source_case_id="US-bullish",
        repetition=1,
        runtime=EvaluationRuntimeIdentity(
            commit_sha="b" * 40,
            provider="fixture",
            quick_model="fixture-model",
            deep_model="fixture-model",
            output_language="en",
        ),
        prompt_hash=canonical_hash("prompt"),
        runtime_prompt_hash=canonical_hash("runtime prompt"),
        evidence_hash=evidence.digest,
        output_hash=canonical_hash("output"),
        metrics=RunMetrics(),
        risk_recall=1.0,
        evidence=evidence,
        reports={},
        raw_baseline_output={"report": "Raw main report."},
    )
    review = EvalReview(
        record_id="main_analyst:other:1",
        quality=QualityScores(
            factual_completeness=0.8,
            analytical_depth=0.8,
            table_readability=0.8,
            decision_utility=0.8,
        ),
        reviewer="blind-reviewer",
        rubric_version="quality-v1",
    )

    with pytest.raises(ValueError, match="does not identify"):
        measurement_from_record(
            record,
            review,
            artifact_path="records.jsonl#record",
        )


@pytest.mark.unit
def test_live_evaluation_requires_both_explicit_gates(monkeypatch) -> None:
    monkeypatch.delenv("RUN_LIVE_LLM_EVALS", raising=False)

    with pytest.raises(RuntimeError, match="Live evaluation is disabled"):
        _require_live_authorization(SimpleNamespace(execute=True))

    monkeypatch.setenv("RUN_LIVE_LLM_EVALS", "1")
    with pytest.raises(RuntimeError, match="Live evaluation is disabled"):
        _require_live_authorization(SimpleNamespace(execute=False))

    _require_live_authorization(SimpleNamespace(execute=True))


@pytest.mark.unit
def test_main_adapter_record_matches_neutral_v2_record_contract() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="baseline",
        requested_date=date(2026, 7, 24),
        content="Frozen baseline evidence.",
    )
    evidence = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    prompt_hash = canonical_hash("baseline prompt contract")
    runtime_prompt_hash = canonical_hash("runtime prompt trace")
    payload = main_adapter._record(
        args=SimpleNamespace(
            worktree_commit="b" * 40,
            provider="fixture",
            quick_model="fixture-model",
            deep_model="fixture-model",
            quick_reasoning="medium",
            deep_reasoning="medium",
            output_language="en",
            temperature=0.0,
        ),
        case={
            "case_id": "US-bullish",
            "evidence": evidence.model_dump(mode="json"),
        },
        record_case_id="US-bullish:market",
        evidence=evidence.model_dump(mode="json"),
        repetition=1,
        variant="main_analyst",
        reports={},
        output="Baseline report.",
        tracker=SimpleNamespace(
            prompt_hash=runtime_prompt_hash,
            llm_calls=1,
            input_tokens=100,
            output_tokens=50,
            elapsed=2.0,
        ),
        prompt_hash=prompt_hash,
        risk_recall=1.0,
        issues=[],
    )

    record = RecordedEvalOutput.model_validate(payload)

    assert record.prompt_hash == prompt_hash
    assert record.runtime_prompt_hash == runtime_prompt_hash
    assert record.raw_baseline_output == {"report": "Baseline report."}
