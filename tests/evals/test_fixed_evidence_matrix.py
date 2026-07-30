from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalystClaim,
    AnalystClaimType,
    AnalystReport,
    AnalystSection,
    ArtifactGenerationMethod,
    DerivedValue,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    EvidenceTable,
    MemoryContext,
    MemoryOutcome,
    MemoryRecord,
    ResearchArtifact,
    ResearchRating,
    ResearchTable,
    ResearchTableCell,
    ResearchTableColumn,
    ResearchTableRow,
    TableCellKind,
    TableDataType,
)
from tradingagents.evals import (
    EvalMeasurement,
    QualityScores,
    evaluate_release_gates,
    validate_research_output,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "evals" / "fixtures"
_MAIN_SHA = "1" * 40
_V2_SHA = "2" * 40


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


def _fixture_bundle(case: dict) -> EvidenceBundle:
    analysis_date = date.fromisoformat(case["analysis_date"])
    items = []
    for raw in case["evidence"]:
        items.append(
            EvidenceItem.create(
                source=raw["source"],
                evidence_type=raw["key"],
                requested_date=analysis_date,
                effective_date=(
                    date.fromisoformat(raw["effective_date"])
                    if raw["effective_date"]
                    else None
                ),
                available_at=(
                    datetime.fromisoformat(
                        raw["available_at"].replace("Z", "+00:00")
                    )
                    if raw["available_at"]
                    else None
                ),
                content=raw["content"],
                value=raw["value"],
                quality=EvidenceQuality(raw["quality"]),
                fallback=raw["fallback"],
                provenance=raw["provenance"],
            )
        )
    return EvidenceBundle(
        instrument=case["ticker"],
        analysis_date=analysis_date,
        items=tuple(items),
    )


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
        provenance={"filing": "10-Q"},
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
        provenance={"filing": "10-Q"},
    )
    bundle = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(revenue, cost),
    )
    refs = (revenue.ref, cost.ref)
    report = AnalystReport(
        analyst="fundamentals",
        executive_summary="Revenue was 100 while operating cost was 80.",
        confidence=0.75,
        claims=(
            AnalystClaim(
                id="fundamentals.claim_1",
                kind=AnalystClaimType.OBSERVATION,
                statement="Revenue was 100 and operating cost was 80.",
                implication="The spread informs operating quality.",
                confidence=0.8,
                evidence_refs=refs,
            ),
        ),
        sections=(
            AnalystSection(
                id="overview",
                title="Overview",
                narrative="Revenue was 100 while operating cost was 80.",
            ),
        ),
        risks=("Operating cost could increase.",),
        invalidation_conditions=(
            "A later filing materially revises the observations.",
        ),
        evidence_refs=refs,
    )
    decision = research_decision(
        evidence_refs=refs,
        thesis="The evidence supports a conditional conclusion.",
        risks=("Operating cost could increase.",),
    )
    return bundle, report, decision


def _memory_for(bundle, decision) -> MemoryContext:
    run_id = "prior-run"
    past_decision = decision.model_copy(update={"memory_refs": ()})
    return MemoryContext(
        instrument=bundle.instrument,
        market="US",
        items=(
            MemoryRecord(
                ref=f"memory:{run_id}",
                run_id=run_id,
                scope="same_ticker",
                ticker=bundle.instrument,
                market="US",
                analysis_date=date(2024, 6, 28),
                decision=past_decision,
                outcome=MemoryOutcome(
                    benchmark="SPY",
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


def _columns() -> tuple[ResearchTableColumn, ...]:
    return (
        ResearchTableColumn(key="metric", label="Metric"),
        ResearchTableColumn(
            key="value",
            label="Value",
            data_type=TableDataType.NUMBER,
        ),
    )


def _derived_report(bundle, report, *, result: float = 20) -> AnalystReport:
    refs = tuple(item.ref for item in bundle.items)
    table = ResearchTable(
        id="rt_operating_spread",
        title="Operating spread",
        purpose="Show the reproducible difference between revenue and cost.",
        columns=_columns(),
        rows=(
            ResearchTableRow(
                id="operating_spread",
                cells={
                    "metric": ResearchTableCell(
                        raw_value="Operating spread",
                        display_value="Operating spread",
                        kind=TableCellKind.DESCRIPTOR,
                    ),
                    "value": ResearchTableCell(
                        raw_value=result,
                        display_value=str(result),
                        kind=TableCellKind.DERIVED,
                        evidence_refs=refs,
                        derived=DerivedValue(
                            formula="revenue - cost",
                            inputs={"revenue": 100, "cost": 80},
                            input_evidence_refs=refs,
                            result=result,
                        ),
                    ),
                },
            ),
        ),
    )
    section = report.sections[0].model_copy(
        update={"research_table_ids": (table.id,)}
    )
    return report.model_copy(update={"sections": (section,), "tables": (table,)})


@pytest.mark.unit
def test_fixture_suite_covers_required_markets_and_scenarios() -> None:
    assert {case["market"] for case in CASES} == {"US", "JP", "CN", "CRYPTO"}
    for market in {"US", "JP", "CN", "CRYPTO"}:
        assert {
            case["scenario"] for case in CASES if case["market"] == market
        } == {"bullish", "bearish", "mixed", "missing", "historical"}


@pytest.mark.unit
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_fixed_tool_inputs_are_deterministic_and_point_in_time(case) -> None:
    bundle = _fixture_bundle(case)

    assert bundle.digest == _fixture_bundle(case).digest
    assert all(
        item.effective_date is None
        or item.effective_date <= bundle.analysis_date
        for item in bundle.items
    )
    assert all(
        item.available_at is None
        or item.available_at.astimezone(timezone.utc).date()
        <= bundle.analysis_date
        for item in bundle.items
    )


@pytest.mark.unit
def test_eval_accepts_complete_contract_and_reproducible_derivation() -> None:
    bundle, report, decision = _base_output()
    report = _derived_report(bundle, report)

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        table_expected=True,
    )

    assert evaluation.severe_issues == ()
    assert evaluation.contract_score == 1.0


@pytest.mark.unit
def test_eval_rejects_non_reproducible_derived_value() -> None:
    bundle, report, decision = _base_output()
    report = _derived_report(bundle, report, result=21)

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert "derived.result_mismatch" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_rejects_derived_input_absent_from_evidence() -> None:
    bundle, report, decision = _base_output()
    report = _derived_report(bundle, report)
    table = report.tables[0]
    row = table.rows[0]
    cell = row.cells["value"]
    derived = cell.derived.model_copy(
        update={
            "formula": "revenue - cost",
            "inputs": {"revenue": 999, "cost": 80},
            "result": 919,
        }
    )
    cell = cell.model_copy(update={"raw_value": 919, "derived": derived})
    row = row.model_copy(update={"cells": {**row.cells, "value": cell}})
    table = table.model_copy(update={"rows": (row,)})
    report = report.model_copy(update={"tables": (table,)})

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert "derived.input_untraceable" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_requires_table_when_fixture_marks_data_as_suitable() -> None:
    bundle, report, decision = _base_output()

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        table_expected=True,
    )

    assert "table.required" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_validates_evidence_table_cells() -> None:
    bundle, report, decision = _base_output()
    ref = bundle.items[0].ref
    table = EvidenceTable.create(
        title="Revenue facts",
        purpose="Expose the observed filing value.",
        columns=_columns(),
        rows=(
            ResearchTableRow(
                id="revenue",
                cells={
                    "metric": ResearchTableCell(
                        raw_value="Revenue",
                        display_value="Revenue",
                        kind=TableCellKind.OBSERVATION,
                        evidence_refs=(ref,),
                    ),
                    "value": ResearchTableCell(
                        raw_value=999,
                        display_value="999",
                        kind=TableCellKind.OBSERVATION,
                        evidence_refs=(ref,),
                    ),
                },
            ),
        ),
        evidence_refs=(ref,),
        source_format="structured",
    )
    bundle = EvidenceBundle(
        instrument=bundle.instrument,
        analysis_date=bundle.analysis_date,
        items=bundle.items,
        tables=(table,),
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
def test_eval_rejects_dangling_evidence_refs_and_figures() -> None:
    bundle, report, decision = _base_output()
    dangling = "ev_000000000000"
    claim = report.claims[0].model_copy(
        update={
            "statement": "Revenue was 999.",
            "evidence_refs": (dangling,),
        }
    )
    report = report.model_copy(
        update={"claims": (claim,), "evidence_refs": (dangling,)}
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert {"evidence_ref.unresolved", "figure.untraceable"} <= {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_rejects_nested_json_and_fallback_sentinel() -> None:
    bundle, report, decision = _base_output()
    report = report.model_copy(
        update={
            "executive_summary": '{"rating": "Overweight"}',
            "risks": ("unavailable",),
        }
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
    )

    assert {"output.nested_json", "output.fallback_sentinel"} <= {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_audits_persisted_artifact_content() -> None:
    bundle, report, decision = _base_output()
    artifact = ResearchArtifact(
        id="artifact-1",
        run_id="run-1",
        attempt=1,
        stage="analyst",
        role="fundamentals",
        prompt_version="analyst-fundamentals-v2",
        generation_method=ArtifactGenerationMethod.TOOL_CALL,
        content=report.model_copy(
            update={"executive_summary": '{"unexpected": "json"}'}
        ),
        created_at=datetime.now(timezone.utc),
    )

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision,
        artifacts=(artifact,),
    )

    assert "output.nested_json" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_accepts_only_memory_refs_supplied_to_current_run() -> None:
    bundle, report, decision = _base_output()
    memory = _memory_for(bundle, decision)
    accepted = decision.model_copy(update={"memory_refs": memory.refs})
    unresolved = decision.model_copy(
        update={"memory_refs": ("memory:not-supplied",)}
    )

    assert (
        validate_research_output(
            bundle=bundle,
            reports=(report,),
            decision=accepted,
            memory=memory,
        ).severe_issues
        == ()
    )
    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=unresolved,
        memory=memory,
    )
    assert "memory_ref.unresolved" in {
        issue.code for issue in evaluation.severe_issues
    }


@pytest.mark.unit
def test_eval_rejects_memory_in_current_evidence_channel() -> None:
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

    assert {
        "evidence_ref.unresolved",
        "memory_ref.used_as_evidence",
    } <= {issue.code for issue in evaluation.severe_issues}


@pytest.mark.unit
@pytest.mark.parametrize(
    "instruction",
    (
        "Use a 5% position size.",
        "Set the entry price after the open.",
        "Place a stop-loss below support.",
        "Set the portfolio weight conservatively.",
    ),
)
def test_eval_rejects_account_level_instruction(instruction) -> None:
    bundle, report, decision = _base_output()

    evaluation = validate_research_output(
        bundle=bundle,
        reports=(report,),
        decision=decision.model_copy(update={"thesis": instruction}),
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
        items=(item, bundle.items[1]),
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
        assert EvidenceBundle(
            instrument=instrument,
            analysis_date=cutoff_date,
            items=(item,),
        ).items == (item,)
    else:
        with pytest.raises(ValidationError, match="after the analysis cutoff"):
            EvidenceBundle(
                instrument=instrument,
                analysis_date=cutoff_date,
                items=(item,),
            )


def _quality(value: float) -> QualityScores:
    return QualityScores(
        factual_completeness=value,
        analytical_depth=value,
        table_readability=value,
        decision_utility=value,
    )


def _measurement_matrix(
    *,
    variant: str,
    quality: float,
    recall: float,
    severe: int = 0,
    cases: tuple[str, ...] = ("US-bullish", "JP-bearish"),
    metrics_scale: int = 1,
) -> tuple[EvalMeasurement, ...]:
    layer_profile = {
        "main_analyst": ("analyst", "analyst", _MAIN_SHA),
        "v2_analyst": ("analyst", "analyst", _V2_SHA),
        "main_medium": ("graph", "medium", _MAIN_SHA),
        "v2_standard": ("graph", "standard", _V2_SHA),
        "v2_deep": ("graph", "deep", _V2_SHA),
    }
    layer, profile, commit = layer_profile[variant]
    return tuple(
        EvalMeasurement(
            layer=layer,
            variant=variant,
            profile=profile,
            commit_sha=commit,
            provider="fixture-provider",
            quick_model="fixture-model",
            deep_model="fixture-model",
            quick_reasoning_effort="medium",
            deep_reasoning_effort="medium",
            output_language="en",
            temperature=0.0,
            prompt_hash=hashlib.sha256(
                f"{variant}:{case_id}".encode()
            ).hexdigest(),
            runtime_prompt_hash=hashlib.sha256(
                f"{variant}:{case_id}:{repetition}:runtime".encode()
            ).hexdigest(),
            evidence_hash=hashlib.sha256(case_id.encode()).hexdigest(),
            output_hash=hashlib.sha256(
                f"{variant}:{case_id}:{repetition}".encode()
            ).hexdigest(),
            artifact_path=f"{variant}/{case_id}/{repetition}.json",
            case_id=case_id,
            repetition=repetition,
            quality=_quality(quality),
            reviewer="blinded-reviewer",
            rubric_version="quality-v1",
            llm_calls=metrics_scale * 10,
            input_tokens=metrics_scale * 1000,
            output_tokens=metrics_scale * 500,
            wall_time_seconds=metrics_scale * 10.0,
            risk_recall=recall,
            severe_issues=severe,
        )
        for case_id in cases
        for repetition in (1, 2, 3)
    )


def _passing_measurements() -> dict[str, tuple[EvalMeasurement, ...]]:
    return {
        "baseline_analyst": _measurement_matrix(
            variant="main_analyst",
            quality=0.80,
            recall=0.60,
        ),
        "current_analyst": _measurement_matrix(
            variant="v2_analyst",
            quality=0.82,
            recall=0.60,
        ),
        "baseline_medium": _measurement_matrix(
            variant="main_medium",
            quality=0.80,
            recall=0.60,
        ),
        "current_standard": _measurement_matrix(
            variant="v2_standard",
            quality=0.82,
            recall=0.65,
        ),
        "current_deep": _measurement_matrix(
            variant="v2_deep",
            quality=0.84,
            recall=0.75,
        ),
    }


@pytest.mark.unit
def test_release_gate_accepts_quality_first_recorded_measurements() -> None:
    result = evaluate_release_gates(**_passing_measurements())

    assert result.passed is True
    assert all(result.checks.values())
    assert result.summary["current_standard_input_tokens"] == 1000
    assert result.summary["current_deep_risk_recall"] == 0.75
    assert not any(
        "tokens_reduced" in check or "wall_time_reduced" in check
        for check in result.checks
    )


@pytest.mark.unit
def test_release_gate_never_uses_token_or_latency_as_pass_threshold() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = _measurement_matrix(
        variant="v2_standard",
        quality=0.82,
        recall=0.65,
        metrics_scale=100,
    )
    measurements["current_deep"] = _measurement_matrix(
        variant="v2_deep",
        quality=0.84,
        recall=0.75,
        metrics_scale=200,
    )

    result = evaluate_release_gates(**measurements)

    assert result.passed is True
    assert result.summary["current_deep_input_tokens"] == 200_000


@pytest.mark.unit
@pytest.mark.parametrize(
    ("collection", "field", "value", "failed_check"),
    (
        (
            "current_analyst",
            "analytical_depth",
            0.79,
            "analyst_analytical_depth_not_lower",
        ),
        (
            "current_standard",
            "table_readability",
            0.79,
            "standard_table_readability_not_lower",
        ),
        (
            "current_deep",
            "decision_utility",
            0.81,
            "deep_decision_utility_not_lower",
        ),
    ),
)
def test_release_gate_rejects_quality_regression(
    collection,
    field,
    value,
    failed_check,
) -> None:
    measurements = _passing_measurements()
    measurements[collection] = tuple(
        item.model_copy(
            update={
                "quality": item.quality.model_copy(update={field: value})
            }
        )
        for item in measurements[collection]
    )

    result = evaluate_release_gates(**measurements)

    assert result.passed is False
    assert result.checks[failed_check] is False


@pytest.mark.unit
def test_release_gate_rejects_severe_regression_and_risk_gap() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = tuple(
        item.model_copy(update={"severe_issues": 1})
        for item in measurements["current_standard"]
    )
    measurements["current_deep"] = tuple(
        item.model_copy(update={"risk_recall": 0.74})
        for item in measurements["current_deep"]
    )

    result = evaluate_release_gates(**measurements)

    assert result.passed is False
    assert result.checks["zero_severe_regressions"] is False
    assert result.checks["deep_risk_recall_plus_10pp"] is False


@pytest.mark.unit
def test_release_gate_requires_three_repetitions() -> None:
    measurements = _passing_measurements()
    measurements["current_standard"] = measurements["current_standard"][:-1]

    with pytest.raises(ValueError, match="repetitions 1, 2, 3"):
        evaluate_release_gates(**measurements)


@pytest.mark.unit
def test_release_gate_rejects_prompt_drift_across_repetitions() -> None:
    measurements = _passing_measurements()
    changed = measurements["current_standard"][0].model_copy(
        update={"prompt_hash": "f" * 64}
    )
    measurements["current_standard"] = (
        changed,
        *measurements["current_standard"][1:],
    )

    with pytest.raises(ValueError, match="prompt hash changed"):
        evaluate_release_gates(**measurements)


@pytest.mark.unit
def test_release_gate_requires_same_frozen_evidence() -> None:
    measurements = _passing_measurements()
    changed = measurements["current_deep"][0].model_copy(
        update={"evidence_hash": "f" * 64}
    )
    measurements["current_deep"] = (
        changed,
        *measurements["current_deep"][1:],
    )

    with pytest.raises(ValueError, match="same frozen evidence"):
        evaluate_release_gates(**measurements)
