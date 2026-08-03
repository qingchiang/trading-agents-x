"""Deidentified regressions distilled from the 2026-08-03 live runs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tests.factories import research_decision
from tradingagents.application.contracts import (
    EvidenceBundle,
    EvidenceItem,
    NumericAuditStatus,
    NumericDisplayScale,
    NumericDisplayStatus,
)
from tradingagents.application.evidence import extract_evidence_tables
from tradingagents.graph.deliberation import (
    CalculationInputDraft,
    CalculationRecordDraft,
    DecisionNumericDraft,
    DecisionNumericRequirementDraft,
    ResearchDecisionCoreEnvelope,
    _assemble_numeric_draft,
    _preflight_numeric_requirements,
)
from tradingagents.graph.numeric_evidence import build_numeric_value_catalog

_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "live_numeric_regressions_2026_08_03.json"
)


def _payload() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _bundle(instrument: str) -> EvidenceBundle:
    item = EvidenceItem(
        ref="ev_20260803abcd",
        source="deidentified live regression",
        evidence_type="numeric audit input",
        requested_date=date(2026, 8, 3),
        effective_date=date(2026, 8, 1),
    )
    return EvidenceBundle(
        instrument=instrument,
        analysis_date=date(2026, 8, 3),
        items=(item,),
    )


def _inputs(values: dict[str, float]) -> tuple[CalculationInputDraft, ...]:
    return tuple(
        CalculationInputDraft(name=name, value=value)
        for name, value in values.items()
    )


def _core_payload(ref: str) -> dict[str, Any]:
    payload = research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    for key in (
        "valuation_assessment",
        "market_reference_levels",
        "calculation_records",
        "numeric_audit_status",
    ):
        payload.pop(key, None)
    for scenario in payload["scenarios"]:
        scenario.pop("reference_ranges", None)
    return payload


def test_fixture_manifest_covers_every_authorized_live_sample() -> None:
    payload = _payload()
    represented = {case["instrument"] for case in payload["comparison_cases"]}
    represented.add(payload["unicode_operand_case"]["instrument"])
    represented.add(payload["derived_range_case"]["instrument"])
    represented.update(case["instrument"] for case in payload["catalog_cases"])

    assert payload["schema_version"] == "1"
    assert represented == set(payload["covered_instruments"])
    assert represented == {
        "4568.T",
        "6501.T",
        "7011.T",
        "8058.T",
        "AAPL",
        "600487.SS",
        "NVDA",
        "002028.SZ",
        "GOOG",
        "600176.SS",
        "601208.SS",
    }


@pytest.mark.parametrize(
    "case",
    _payload()["comparison_cases"],
    ids=lambda case: case["id"],
)
def test_live_numeric_comparison_regressions(case: dict[str, Any]) -> None:
    bundle = _bundle(case["instrument"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id=f"req_{case['id']}",
        component_path=case["component_path"],
        label=case["label"],
        stated_value=case["stated_value"],
        fraction_digits=case["fraction_digits"],
        formula=case["formula"],
        inputs=_inputs(case["inputs"]),
        input_evidence_refs=(ref,),
        unit=case["unit"],
        display_scale=NumericDisplayScale(case["display_scale"]),
        limitations=("Deidentified live regression.",),
    )
    calculation = CalculationRecordDraft(
        id=f"calc_{case['id']}",
        formula=requirement.formula,
        inputs=requirement.inputs,
        input_evidence_refs=requirement.input_evidence_refs,
        unit=requirement.unit,
        limitations=requirement.limitations,
        requirement_ids=(requirement.id,),
    )

    result = _assemble_numeric_draft(
        DecisionNumericDraft(requested=True, calculation_records=(calculation,)),
        bundle=bundle,
        allowed_evidence_refs={ref},
        value_catalog=build_numeric_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
        requirements=(requirement,),
    )

    check = result.requirement_checks[0]
    assert check.comparison_result == pytest.approx(
        case["expected_comparison_result"]
    )
    assert check.display_status is NumericDisplayStatus(
        case["expected_display_status"]
    )
    assert result.status is NumericAuditStatus(case["expected_numeric_status"])


def test_live_unicode_operand_regression_is_deterministically_normalized() -> None:
    case = _payload()["unicode_operand_case"]
    bundle = _bundle(case["instrument"])
    ref = bundle.items[0].ref
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **_core_payload(ref),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": [
                {
                    "id": f"req_{case['id']}",
                    "component_path": case["component_path"],
                    "label": case["label"],
                    "stated_value": case["stated_value"],
                    "fraction_digits": case["fraction_digits"],
                    "formula": case["formula"],
                    "inputs": case["inputs"],
                    "input_evidence_refs": [ref],
                    "unit": case["unit"],
                    "display_scale": case["display_scale"],
                    "limitations": ["Deidentified live regression."],
                }
            ],
        }
    )

    preflight = _preflight_numeric_requirements(
        envelope,
        valid_evidence_refs={ref},
    )

    assert preflight.issues == ()
    requirement = preflight.requirements[0]
    assert requirement.formula == case["expected_formula"]
    assert [item.name for item in requirement.inputs] == case["expected_input_names"]


def test_live_derived_range_regression_keeps_independent_endpoints() -> None:
    case = _payload()["derived_range_case"]
    bundle = _bundle(case["instrument"])
    ref = bundle.items[0].ref
    candidates = []
    for item in case["requirements"]:
        candidates.append(
            {
                **item,
                "component_path": "scenarios.bull.outcome",
                "label": item["id"],
                "fraction_digits": 1,
                "inputs": [
                    {"name": name, "value": value}
                    for name, value in item["inputs"].items()
                ],
                "input_evidence_refs": [ref],
                "unit": "JPY",
                "display_scale": "base",
                "display_group_id": case["display_group_id"],
                "limitations": ["Deidentified live regression."],
            }
        )
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **_core_payload(ref),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": candidates,
        }
    )

    preflight = _preflight_numeric_requirements(
        envelope,
        valid_evidence_refs={ref},
    )

    assert preflight.issues == ()
    assert [item.display_role for item in preflight.requirements] == [
        "range_low",
        "range_high",
    ]
    assert len({item.formula for item in preflight.requirements}) == 2


@pytest.mark.parametrize(
    "case",
    _payload()["catalog_cases"],
    ids=lambda case: case["id"],
)
def test_live_target_catalog_regressions_keep_values_and_dates(
    case: dict[str, Any],
) -> None:
    facts = [
        {
            **fact,
            "label": fact["key"].replace("_", " "),
            "measurement_kind": "currency",
            "unit": case["unit"],
            "effective_date": case["effective_date"],
        }
        for fact in case["facts"]
    ]
    item = EvidenceItem.create(
        source="deidentified sell-side targets",
        evidence_type="analyst targets",
        requested_date=date.fromisoformat(case["effective_date"]),
        content="Readable target narrative without a generated numeric table.",
        provenance={"structured_numeric_facts": facts},
    )
    table = extract_evidence_tables((item,))[0]
    bundle = EvidenceBundle(
        instrument=case["instrument"],
        analysis_date=date.fromisoformat(case["effective_date"]),
        items=(item,),
        tables=(table,),
    )

    catalog = build_numeric_value_catalog(bundle)

    assert {entry.value for entry in catalog} == {
        fact["value"] for fact in case["facts"]
    }
    assert {entry.unit for entry in catalog} == {case["unit"]}
    assert {entry.observed_date.isoformat() for entry in catalog} == {
        case["effective_date"]
    }
