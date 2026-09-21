from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from tests.support.synthesis import (
    _live_numeric_fixture,
    _numeric_3778_payload,
    _numeric_regression,
    _numeric_regression_payload,
    _state,
    _value_catalog,
)
from tradingagents.domain.common import (
    NumericAuditStatus,
    NumericCalculationStatus,
    NumericDisplayScale,
    NumericDisplayStatus,
    ResearchScenarioKind,
    ScenarioReferenceCategory,
)
from tradingagents.domain.decision import (
    NumericTemporalBasis,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
    EvidenceItem,
    MeasurementKind,
)
from tradingagents.domain.numeric_audit import MarketReferenceBasis
from tradingagents.research.synthesis.drafts import (
    CalculationInputDraft,
    CalculationRecordDraft,
    DecisionNumericDraft,
    DecisionNumericRequirementDraft,
    DerivedRangeEndpointDraft,
    InterpretedRangeEndpointDraft,
    ObservedMarketReferenceLevelDraft,
    ObservedRangeEndpointDraft,
    ScenarioReferenceRangeDraft,
    ScenarioReferenceRangesDraft,
    ValuationAssessmentDraft,
)
from tradingagents.research.synthesis.numeric_audit import _assemble_numeric_draft
from tradingagents.research.synthesis.numeric_evidence import build_numeric_value_catalog
from tradingagents.research.synthesis.numeric_generation import (
    _emit_numeric_normalization_event,
)
from tradingagents.research.synthesis.output_validation import OutputValidationError


def test_calculation_date_refs_ignore_undated_background_evidence() -> None:
    dated = EvidenceItem.create(
        source="statement",
        evidence_type="dated scalar",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        value=10,
    )
    background = EvidenceItem.create(
        source="news",
        evidence_type="undated background",
        requested_date=date(2026, 8, 1),
        content="Context only.",
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=(dated, background),
    )
    requirement = DecisionNumericRequirementDraft(
        id="req_dated",
        component_path="thesis",
        label="Dated calculation",
        stated_value=20,
        fraction_digits=0,
        formula="value * multiplier",
        inputs=(
            CalculationInputDraft(
                name="value",
                value=10,
                date_evidence_refs=(dated.ref,),
            ),
            CalculationInputDraft(name="multiplier", value=2),
        ),
        input_evidence_refs=(dated.ref, background.ref),
        unit="JPY",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Background does not establish the input date.",),
    )
    draft = DecisionNumericDraft(
        requested=True,
        calculation_records=(
            CalculationRecordDraft(
                id="calc_dated",
                formula="value * multiplier",
                inputs=(
                    CalculationInputDraft(
                        name="value",
                        value=10,
                        date_evidence_refs=(dated.ref,),
                    ),
                    CalculationInputDraft(name="multiplier", value=2),
                ),
                input_evidence_refs=(dated.ref, background.ref),
                unit="JPY",
                limitations=("Background does not establish the input date.",),
                requirement_ids=(requirement.id,),
            ),
        ),
    )

    assembly = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={dated.ref, background.ref},
        value_catalog={},
        salvage=False,
        node="committee.final.serialize.numeric",
        requirements=(requirement,),
    )

    assert assembly.calculation_records[0].as_of_date == date(2026, 7, 31)
    assert assembly.calculation_records[0].date_evidence_refs == (dated.ref,)


def test_4483_salvage_contains_invalid_date_ref_relationship() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    first_ref = bundle.items[0].ref
    other = EvidenceItem.create(
        source="fixture.other",
        evidence_type="market snapshot",
        requested_date=bundle.analysis_date,
        effective_date=bundle.analysis_date,
        value=2,
        unit="USD",
    )
    bundle = EvidenceBundle(
        instrument=bundle.instrument,
        analysis_date=bundle.analysis_date,
        items=(*bundle.items, other),
        sealed_at=bundle.sealed_at,
    )
    requirement = DecisionNumericRequirementDraft(
        id="req_invalid_date_ref",
        component_path="thesis",
        label="Invalid date ref",
        stated_value=50,
        fraction_digits=1,
        formula="value / divisor",
        inputs=(
            CalculationInputDraft(
                name="value",
                value=100,
                date_evidence_refs=(other.ref,),
            ),
            CalculationInputDraft(name="divisor", value=2),
        ),
        input_evidence_refs=(first_ref,),
        unit="x",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Fixture limitation.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_invalid_date_ref",
        formula=requirement.formula,
        inputs=requirement.inputs,
        input_evidence_refs=requirement.input_evidence_refs,
        unit=requirement.unit,
        limitations=requirement.limitations,
        requirement_ids=(requirement.id,),
    )

    assembly = _assemble_numeric_draft(
        DecisionNumericDraft(
            requested=True,
            calculation_records=(calculation,),
        ),
        bundle=bundle,
        allowed_evidence_refs={first_ref, other.ref},
        value_catalog=build_numeric_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
        requirements=(requirement,),
    )

    check = assembly.requirement_checks[0]
    assert assembly.status is NumericAuditStatus.PARTIAL
    assert check.calculation_status is NumericCalculationStatus.INVALID
    assert check.display_status is NumericDisplayStatus.NOT_CHECKED
    assert check.date_evidence_refs == ()
    assert "numeric.requirement.req_invalid_date_ref.date_refs.not_input_refs" in (
        check.issue_codes
    )


def test_display_mismatch_keeps_verified_calculation_and_comparison() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id="req_guidance_pe",
        component_path="thesis",
        label="Forward PE",
        stated_value=45.8,
        fraction_digits=1,
        formula="price / guidance_eps",
        inputs=(
            CalculationInputDraft(name="price", value=3834.343755),
            CalculationInputDraft(name="guidance_eps", value=1),
        ),
        input_evidence_refs=(ref,),
        unit="x",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Guidance may change.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_guidance_pe",
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
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
        requirements=(requirement,),
    )

    assert result.status is NumericAuditStatus.PARTIAL
    assert len(result.calculation_records) == 1
    assert result.calculation_records[0].decision_uses[0].component_path == "thesis"
    assert result.omissions == ()
    assert result.audit is not None
    check = result.audit.requirement_checks[0]
    assert check.calculation_status is NumericCalculationStatus.VERIFIED
    assert check.display_status is NumericDisplayStatus.MISMATCHED
    assert check.stated_value == 45.8
    assert check.canonical_result == pytest.approx(3834.343755)
    assert check.rounded_stated_value == 45.8
    assert check.rounded_canonical_result == 3834.3
    assert check.issue_codes == ("numeric.requirement.req_guidance_pe.result_mismatch",)


def test_one_display_unit_difference_is_approximately_matched() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id="req_growth",
        component_path="thesis",
        label="Revenue growth",
        stated_value=85.24,
        fraction_digits=2,
        formula="(current_revenue - prior_revenue) / prior_revenue",
        inputs=(
            CalculationInputDraft(name="current_revenue", value=185.22763378875221),
            CalculationInputDraft(name="prior_revenue", value=100),
        ),
        input_evidence_refs=(ref,),
        unit="%",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Fixture calculation.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_growth",
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
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
        requirements=(requirement,),
    )

    assert result.status is NumericAuditStatus.COMPLETE
    assert result.warnings == ()
    check = result.requirement_checks[0]
    assert check.display_status is NumericDisplayStatus.APPROXIMATELY_MATCHED
    assert check.rounded_stated_value == 85.24
    assert check.rounded_canonical_result == 85.23
    assert check.issue_codes == ("numeric.requirement.req_growth.display_approximate",)


def test_percent_formula_using_display_scale_reports_specific_mismatch() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id="req_target_upside",
        component_path="thesis",
        label="Target upside",
        stated_value=45.46,
        fraction_digits=2,
        formula="((target_price - close_price) / close_price) * 100",
        inputs=(
            CalculationInputDraft(name="target_price", value=145.46),
            CalculationInputDraft(name="close_price", value=100),
        ),
        input_evidence_refs=(ref,),
        unit="%",
        display_scale=NumericDisplayScale.BASE,
        limitations=("The target may change.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_target_upside",
        formula=requirement.formula,
        inputs=requirement.inputs,
        input_evidence_refs=requirement.input_evidence_refs,
        unit=requirement.unit,
        limitations=requirement.limitations,
        requirement_ids=(requirement.id,),
    )

    with pytest.raises(OutputValidationError) as error:
        _assemble_numeric_draft(
            DecisionNumericDraft(
                requested=True,
                calculation_records=(calculation,),
            ),
            bundle=bundle,
            allowed_evidence_refs={ref},
            value_catalog=_value_catalog(bundle),
            salvage=False,
            node="committee.final.serialize.numeric",
            requirements=(requirement,),
        )

    assert "numeric.requirement.req_target_upside.percent_scale_mismatch" in error.value.issue_codes


@pytest.mark.parametrize(
    "case",
    _numeric_regression_payload()["scenario_alignment_cases"],
    ids=lambda case: f"{case['ticker']}-{case['label']}",
)
def test_explicit_cross_scenario_label_only_omits_that_range(
    case: dict[str, str],
) -> None:
    bundle, draft = _numeric_regression()
    valid_range = draft.scenario_reference_ranges.base[0]
    mismatched_range = valid_range.model_copy(update={"label": case["label"]})
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (valid_range, mismatched_range)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
        output_language=case["output_language"],
    )

    assert [item.label for item in result.scenario_reference_ranges[ResearchScenarioKind.BASE]] == [
        valid_range.label
    ]
    assert result.status is NumericAuditStatus.PARTIAL
    assert result.issues == (case["expected_issue"],)
    assert {item.component_path for item in result.omissions} == {"numeric.scenario.base.ranges.1"}


def test_non_scenario_purpose_label_is_not_treated_as_misaligned() -> None:
    bundle, draft = _numeric_regression()
    base_range = draft.scenario_reference_ranges.base[0].model_copy(
        update={"label": "下行风险参考区间"}
    )
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (base_range,)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
        output_language="Simplified Chinese (简体中文, zh-CN)",
    )

    assert result.scenario_reference_ranges[ResearchScenarioKind.BASE][0].label == (
        "下行风险参考区间"
    )
    assert result.status is NumericAuditStatus.COMPLETE


def test_6501_numeric_regression_canonicalizes_results_dates_and_shared_usage() -> None:
    bundle, draft = _numeric_regression()

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    calculations = {item.id: item for item in result.calculation_records}
    assert calculations["calc_current_pe"].result == pytest.approx(5267 / 201.14)
    assert calculations["calc_forward_pe"].result == pytest.approx(5267 / 178.13)
    assert calculations["calc_bull_price"].result == pytest.approx(29.76 * 201.14)
    assert calculations["calc_bear_price"].result == pytest.approx(25 * 178.13)
    assert {item.as_of_date for item in calculations.values()} == {date(2026, 7, 31)}
    assert set(result.scenario_reference_ranges) == {
        ResearchScenarioKind.BASE,
        ResearchScenarioKind.BULL,
        ResearchScenarioKind.BEAR,
    }
    assert sum(len(items) for items in result.scenario_reference_ranges.values()) == 4
    assert [item.label for item in result.scenario_reference_ranges[ResearchScenarioKind.BASE]] == [
        "Base technical range",
        "Analyst target range",
    ]
    assert result.valuation_assessment is not None
    assert result.valuation_assessment.as_of_date == date(2026, 7, 31)
    assert result.valuation_assessment.measurement_kind is MeasurementKind.CURRENCY
    assert result.valuation_assessment.unit == "JPY"
    assert len(result.market_reference_levels) == 4
    assert {item.as_of_date for item in result.market_reference_levels} == {
        date(2026, 7, 31),
        date(2026, 8, 1),
    }
    assert result.market_reference_levels[-1].temporal_basis is NumericTemporalBasis.LIVE_SNAPSHOT
    assert set(result.valuation_assessment.calculation_ids) == {
        "calc_bear_price",
        "calc_bull_price",
    }
    assert "calc_current_pe" in result.market_reference_levels[1].calculation_ids
    assert result.status is NumericAuditStatus.COMPLETE
    base_ranges = result.scenario_reference_ranges[ResearchScenarioKind.BASE]
    assert base_ranges[0].low.as_of_date == date(2026, 7, 31)
    assert base_ranges[0].low.date_evidence_refs == ("ev_6501a0000001",)
    assert base_ranges[1].low.as_of_date == date(2026, 8, 1)
    assert base_ranges[1].low.temporal_basis is NumericTemporalBasis.LIVE_SNAPSHOT


def test_valuation_assessment_inherits_ratio_measurement_from_calculations() -> None:
    bundle, source = _numeric_regression()
    calculations = tuple(
        item
        for item in source.calculation_records
        if item.id in {"calc_current_pe", "calc_forward_pe"}
    )
    draft = DecisionNumericDraft(
        requested=True,
        valuation_assessment=ValuationAssessmentDraft(
            method="Forward earnings multiple range",
            low=DerivedRangeEndpointDraft(calculation_id="calc_forward_pe"),
            high=DerivedRangeEndpointDraft(calculation_id="calc_current_pe"),
            limitations=("The forward EPS remains an estimate.",),
        ),
        calculation_records=calculations,
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    valuation = result.valuation_assessment
    assert valuation is not None
    assert valuation.low.value < valuation.high.value
    assert valuation.measurement_kind is MeasurementKind.RATIO
    assert valuation.unit == "x"
    assert result.reordered_ranges == 1
    assert result.status is NumericAuditStatus.COMPLETE


def test_interpreted_range_date_uses_anchor_not_context_evidence() -> None:
    date_case = _numeric_regression_payload()["date_anchor_case"]
    bundle, _draft = _numeric_regression()
    market_item = bundle.items[0]
    context_item = EvidenceItem(
        ref="ev_6501a0000004",
        source="fixture.fundamentals",
        evidence_type="context only",
        requested_date=bundle.analysis_date,
        effective_date=date.fromisoformat(date_case["background_date"]),
        content="Context that explains the scenario but does not set its price date.",
    )
    bundle = bundle.model_copy(update={"items": (*bundle.items, context_item)})
    anchor_ref = build_numeric_value_catalog(bundle)[0].id
    endpoint = InterpretedRangeEndpointDraft(
        value=5000,
        anchor_value_refs=(anchor_ref,),
        context_evidence_refs=(context_item.ref,),
    )
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.TECHNICAL,
                    label="Technical support band",
                    low=endpoint,
                    high=endpoint.model_copy(update={"value": 5300}),
                    interpretation="A rounded technical reference range.",
                    limitations=("The range is not a forecast.",),
                ),
            )
        ),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    reference_range = result.scenario_reference_ranges[ResearchScenarioKind.BASE][0]
    low = reference_range.low
    assert low.as_of_date == date.fromisoformat(date_case["expected_interpreted_date"])
    assert low.date_evidence_refs == (market_item.ref,)
    assert low.evidence_refs == (market_item.ref, context_item.ref)
    assert reference_range.measurement_kind is MeasurementKind.CURRENCY
    assert reference_range.unit == "JPY"


def test_valuation_label_requires_derived_calculation() -> None:
    bundle, draft = _numeric_regression()
    interpreted = draft.scenario_reference_ranges.base[0].model_copy(
        update={"label": "估值回归价格区间"}
    )
    draft = draft.model_copy(
        update={"scenario_reference_ranges": ScenarioReferenceRangesDraft(base=(interpreted,))}
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert result.scenario_reference_ranges == {}
    assert result.issues == ("numeric.scenario.base.ranges.0.derived_calculation_required",)
    assert result.status is NumericAuditStatus.PARTIAL


def test_scenario_ranges_preserve_distinct_ranges_in_the_same_category() -> None:
    bundle, draft = _numeric_regression()
    base_range = draft.scenario_reference_ranges.base[0]
    second_range = base_range.model_copy(
        update={
            "label": "Secondary technical range",
            "low": base_range.low.model_copy(update={"value": 4600}),
            "high": base_range.high.model_copy(update={"value": 7100}),
        }
    )
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (base_range, second_range)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert [item.label for item in result.scenario_reference_ranges[ResearchScenarioKind.BASE]] == [
        "Base technical range",
        "Secondary technical range",
    ]
    assert result.status is NumericAuditStatus.COMPLETE


def test_exact_duplicate_scenario_range_is_removed_without_degrading_audit() -> None:
    bundle, draft = _numeric_regression()
    base_range = draft.scenario_reference_ranges.base[0]
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (base_range, base_range)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert len(result.scenario_reference_ranges[ResearchScenarioKind.BASE]) == 1
    assert result.status is NumericAuditStatus.COMPLETE
    assert result.issues == ()
    assert [warning.code for warning in result.warnings] == ["decision.numeric_duplicate_removed"]


def test_observed_singleton_range_is_promoted_and_deduplicated_by_locator() -> None:
    regression = _numeric_regression_payload()["presentation_regressions"]
    rsi_case = regression["rsi"]
    singleton_case = regression["singleton_reference"]
    item = EvidenceItem(
        ref="ev_0123456789ab",
        source="fixture.market",
        evidence_type="verified RSI",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        value=rsi_case["value"],
        measurement_kind=MeasurementKind(rsi_case["expected_measurement_kind"]),
        unit=rsi_case["expected_unit"],
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=(item,),
    )
    value_ref = build_numeric_value_catalog(bundle)[0].id
    endpoint = ObservedRangeEndpointDraft(value_ref=value_ref)
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.TECHNICAL,
                    label=singleton_case["label"],
                    low=endpoint,
                    high=endpoint,
                    interpretation="Observed momentum reference.",
                    limitations=("A point is not a scenario range.",),
                ),
            )
        ),
        market_reference_levels=(
            ObservedMarketReferenceLevelDraft(
                label="Explicit RSI reference",
                value_ref=value_ref,
                interpretation="Explicit market reference wins deduplication.",
            ),
        ),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert result.scenario_reference_ranges == {}
    assert [level.label for level in result.market_reference_levels] == ["Explicit RSI reference"]
    assert result.market_reference_levels[0].measurement_kind is MeasurementKind.INDEX
    assert result.promoted_singletons == 1
    assert singleton_case["same_locator"] is True
    assert singleton_case["expected_destination"] == "market_reference_levels"
    assert result.status is NumericAuditStatus.COMPLETE
    events: list[dict[str, Any]] = []
    _emit_numeric_normalization_event(
        result,
        event_writer=events.append,
        node="committee.final.serialize.numeric",
    )
    assert events == [
        {
            "event_type": "decision.numeric_singleton_promoted",
            "node": "committee.final.serialize.numeric",
            "payload": {"count": 1},
        }
    ]


def test_equal_observed_values_with_different_locators_are_not_promoted() -> None:
    items = tuple(
        EvidenceItem(
            ref=ref,
            source="fixture.market",
            evidence_type=label,
            requested_date=date(2026, 8, 1),
            effective_date=date(2026, 7, 31),
            value=100,
            measurement_kind=MeasurementKind.CURRENCY,
            unit="JPY",
        )
        for ref, label in (
            ("ev_0123456789ab", "first source"),
            ("ev_abcdef012345", "second source"),
        )
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=items,
    )
    catalog = build_numeric_value_catalog(bundle)
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.TECHNICAL,
                    label="Conflicting singleton",
                    low=ObservedRangeEndpointDraft(value_ref=catalog[0].id),
                    high=ObservedRangeEndpointDraft(value_ref=catalog[1].id),
                    interpretation="Equal values from different locators.",
                    limitations=("The locators differ.",),
                ),
            )
        ),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in items},
        value_catalog={entry.id: entry for entry in catalog},
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert result.market_reference_levels == ()
    assert result.scenario_reference_ranges == {}
    assert result.issues == (
        "numeric.scenario.base.ranges.0.invalid_range",
        "numeric.requested.empty",
    )


def test_interpreted_singleton_is_promoted_to_interpreted_reference() -> None:
    bundle, draft = _numeric_regression()
    source_range = draft.scenario_reference_ranges.base[0]
    singleton = source_range.model_copy(update={"high": source_range.low.model_copy()})
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(base=(singleton,)),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert result.scenario_reference_ranges == {}
    assert len(result.market_reference_levels) == 1
    reference = result.market_reference_levels[0]
    assert reference.basis is MarketReferenceBasis.INTERPRETED
    assert reference.value == singleton.low.value
    assert reference.source_locator is None
    assert result.status is NumericAuditStatus.COMPLETE


def test_derived_singleton_is_promoted_and_keeps_calculation() -> None:
    bundle, draft = _numeric_regression()
    calculation = draft.calculation_records[0]
    endpoint = DerivedRangeEndpointDraft(calculation_id=calculation.id)
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.FUNDAMENTAL,
                    label="Derived earnings reference",
                    low=endpoint,
                    high=endpoint,
                    interpretation="One derived valuation reference.",
                    limitations=("The input assumptions may change.",),
                ),
            )
        ),
        calculation_records=(calculation,),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert len(result.market_reference_levels) == 1
    reference = result.market_reference_levels[0]
    assert reference.basis is MarketReferenceBasis.DERIVED
    assert reference.calculation_ids == (calculation.id,)
    assert [item.id for item in result.calculation_records] == [calculation.id]
    assert result.status is NumericAuditStatus.COMPLETE


def test_reversed_range_and_valuation_are_canonically_ordered() -> None:
    bundle, draft = _numeric_regression()
    source_range = draft.scenario_reference_ranges.base[0]
    reversed_range = source_range.model_copy(
        update={"low": source_range.high, "high": source_range.low}
    )
    valuation = draft.valuation_assessment
    assert valuation is not None
    reversed_valuation = valuation.model_copy(update={"low": valuation.high, "high": valuation.low})
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": ScenarioReferenceRangesDraft(base=(reversed_range,)),
            "valuation_assessment": reversed_valuation,
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assembled_range = result.scenario_reference_ranges[ResearchScenarioKind.BASE][0]
    assert assembled_range.low.value < assembled_range.high.value
    assert result.valuation_assessment is not None
    assert result.valuation_assessment.low.value < result.valuation_assessment.high.value
    assert result.reordered_ranges == 2
    assert result.status is NumericAuditStatus.COMPLETE
    events: list[dict[str, Any]] = []
    _emit_numeric_normalization_event(
        result,
        event_writer=events.append,
        node="committee.final.serialize.numeric",
    )
    assert events == [
        {
            "event_type": "decision.numeric_range_reordered",
            "node": "committee.final.serialize.numeric",
            "payload": {"count": 2},
        }
    ]


def test_3778_singleton_and_reversed_pe_valuation_normalize_without_repair() -> None:
    payload = _numeric_3778_payload()
    evidence = payload["evidence"]
    items = tuple(
        EvidenceItem(
            ref=item["ref"],
            source="fixture.3778",
            evidence_type=name,
            requested_date=payload["analysis_date"],
            effective_date=item["effective_date"],
            value=item["value"],
            measurement_kind=item["measurement_kind"],
            unit=item["unit"],
        )
        for name, item in evidence.items()
    )
    bundle = EvidenceBundle(
        instrument=payload["instrument"],
        analysis_date=payload["analysis_date"],
        items=items,
    )
    catalog = build_numeric_value_catalog(bundle)
    by_ref = {entry.locator.evidence_ref: entry.id for entry in catalog}
    price = evidence["price"]
    company_eps = evidence["company_eps"]
    consensus_eps = evidence["consensus_eps"]
    singleton = payload["interpreted_singleton"]
    valuation = payload["reversed_valuation"]
    calculations = (
        CalculationRecordDraft(
            id="calc_company_pe",
            formula=valuation["low_calculation"],
            inputs=(
                CalculationInputDraft(name="price", value=price["value"]),
                CalculationInputDraft(name="company_eps", value=company_eps["value"]),
            ),
            input_evidence_refs=(price["ref"], company_eps["ref"]),
            unit=valuation["unit"],
            limitations=("Company guidance may change.",),
        ),
        CalculationRecordDraft(
            id="calc_consensus_pe",
            formula=valuation["high_calculation"],
            inputs=(
                CalculationInputDraft(name="price", value=price["value"]),
                CalculationInputDraft(name="consensus_eps", value=consensus_eps["value"]),
            ),
            input_evidence_refs=(price["ref"], consensus_eps["ref"]),
            unit=valuation["unit"],
            limitations=("Consensus coverage is limited.",),
        ),
    )
    endpoint = InterpretedRangeEndpointDraft(
        value=singleton["value"],
        anchor_value_refs=(by_ref[consensus_eps["ref"]],),
    )
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            bull=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.ANALYST_CONSENSUS,
                    label=singleton["label"],
                    low=endpoint,
                    high=endpoint,
                    interpretation="Consensus EPS is a point reference.",
                    limitations=("Coverage is limited.",),
                ),
            )
        ),
        valuation_assessment=ValuationAssessmentDraft(
            method=valuation["method"],
            low=DerivedRangeEndpointDraft(calculation_id="calc_company_pe"),
            high=DerivedRangeEndpointDraft(calculation_id="calc_consensus_pe"),
            limitations=("Both EPS inputs may change.",),
        ),
        calculation_records=calculations,
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in items},
        value_catalog={entry.id: entry for entry in catalog},
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert result.status is NumericAuditStatus.COMPLETE
    assert result.promoted_singletons == 1
    assert result.reordered_ranges == 1
    assert result.scenario_reference_ranges == {}
    assert result.market_reference_levels[0].value == singleton["value"]
    assert result.valuation_assessment is not None
    assert result.valuation_assessment.measurement_kind is MeasurementKind.RATIO
    assert result.valuation_assessment.unit == valuation["unit"]
    assert result.valuation_assessment.low.value == pytest.approx(valuation["expected_low"])
    assert result.valuation_assessment.high.value == pytest.approx(valuation["expected_high"])
    assert payload["no_op_repair"]["initial_digest"] == payload["no_op_repair"]["repair_digest"]


def test_invalid_scenario_range_only_omits_that_range() -> None:
    bundle, draft = _numeric_regression()
    valid_range = draft.scenario_reference_ranges.base[0]
    invalid_range = valid_range.model_copy(
        update={
            "label": "Invalid unsupported range",
            "low": valid_range.low.model_copy(update={"anchor_value_refs": ("nv_ffffffffffff",)}),
        }
    )
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (valid_range, invalid_range)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert [item.label for item in result.scenario_reference_ranges[ResearchScenarioKind.BASE]] == [
        "Base technical range"
    ]
    assert result.status is NumericAuditStatus.PARTIAL
    assert {item.component_path for item in result.omissions} == {"numeric.scenario.base.ranges.1"}
    assert set(result.scenario_reference_ranges) == {
        ResearchScenarioKind.BASE,
        ResearchScenarioKind.BULL,
        ResearchScenarioKind.BEAR,
    }


def test_descriptive_pseudo_formula_does_not_remove_observed_scenario_ranges() -> None:
    bundle, draft = _numeric_regression()
    invalid = CalculationRecordDraft(
        id="calc_descriptive_band",
        formula="ema_to_bollinger",
        inputs=(
            CalculationInputDraft(name="ema", value=5000),
            CalculationInputDraft(name="bollinger", value=5500),
        ),
        input_evidence_refs=(bundle.items[0].ref,),
        unit="JPY",
        limitations=("Descriptive fixture.",),
    )
    draft = draft.model_copy(update={"calculation_records": (*draft.calculation_records, invalid)})

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert set(result.scenario_reference_ranges) == {
        ResearchScenarioKind.BASE,
        ResearchScenarioKind.BULL,
        ResearchScenarioKind.BEAR,
    }
    assert all(item.id != "calc_descriptive_band" for item in result.calculation_records)
    assert "numeric.calculation.calc_descriptive_band.formula.missing_input" in result.issues


@pytest.mark.parametrize(
    ("ticker", "analysis_date", "retrieved_at", "expected_date"),
    (
        ("6501.T", date(2026, 8, 1), "2026-08-01T01:00:00+00:00", date(2026, 8, 1)),
        ("600519.SS", date(2026, 8, 1), "2026-08-01T01:00:00+00:00", date(2026, 8, 1)),
        ("NVDA", date(2026, 7, 31), "2026-08-01T01:00:00+00:00", date(2026, 7, 31)),
    ),
)
def test_live_numeric_evidence_uses_market_local_snapshot_date(
    ticker: str,
    analysis_date: date,
    retrieved_at: str,
    expected_date: date,
) -> None:
    bundle, draft = _live_numeric_fixture(
        ticker=ticker,
        analysis_date=analysis_date,
        retrieved_at=datetime.fromisoformat(retrieved_at),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={bundle.items[0].ref},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    reference = result.market_reference_levels[0]
    assert reference.as_of_date == expected_date
    assert reference.temporal_basis is NumericTemporalBasis.LIVE_SNAPSHOT


@pytest.mark.parametrize(
    ("age_days", "accepted"),
    ((0, True), (5, True), (6, False), (-1, False)),
)
def test_live_numeric_evidence_enforces_near_live_window(
    age_days: int,
    accepted: bool,
) -> None:
    retrieved_at = datetime(2026, 8, 6, 12, tzinfo=UTC)
    bundle, draft = _live_numeric_fixture(
        ticker="6501.T",
        analysis_date=date(2026, 8, 6) - timedelta(days=age_days),
        retrieved_at=retrieved_at,
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={bundle.items[0].ref},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert bool(result.market_reference_levels) is accepted
    if not accepted:
        assert "numeric.market_reference.0.date_unavailable" in result.issues


def test_live_numeric_evidence_rejects_retrieval_after_seal() -> None:
    retrieved_at = datetime(2026, 8, 1, 1, tzinfo=UTC)
    bundle, draft = _live_numeric_fixture(
        ticker="6501.T",
        analysis_date=date(2026, 8, 1),
        retrieved_at=retrieved_at,
        sealed_at=retrieved_at - timedelta(seconds=1),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={bundle.items[0].ref},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert result.market_reference_levels == ()
    assert "numeric.market_reference.0.date_unavailable" in result.issues


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    (
        ("missing_date", "numeric.calculation.calc_current_pe.date_unavailable"),
        ("invalid_ref", "refs.invalid"),
        ("invalid_formula", "numeric.calculation.calc_current_pe.formula.invalid_syntax"),
        ("division_by_zero", "numeric.calculation.calc_current_pe.formula.division_by_zero"),
    ),
)
def test_6501_numeric_regression_keeps_strict_failure_boundaries(
    mutation: str,
    expected_issue: str,
) -> None:
    bundle, draft = _numeric_regression()
    first = draft.calculation_records[0]
    if mutation == "missing_date":
        missing_ref = first.input_evidence_refs[-1]
        bundle = bundle.model_copy(
            update={
                "items": tuple(
                    item.model_copy(update={"effective_date": None})
                    if item.ref == missing_ref
                    else item
                    for item in bundle.items
                )
            }
        )
    elif mutation == "invalid_ref":
        first = first.model_copy(update={"input_evidence_refs": ("ev_ffffffffffff",)})
    elif mutation == "invalid_formula":
        first = first.model_copy(update={"formula": "price +"})
    else:
        first = first.model_copy(
            update={
                "formula": "price / divisor",
                "inputs": (
                    CalculationInputDraft(name="price", value=5267),
                    CalculationInputDraft(name="divisor", value=0),
                ),
            }
        )
    if mutation != "missing_date":
        draft = draft.model_copy(
            update={"calculation_records": (first, *draft.calculation_records[1:])}
        )

    with pytest.raises(OutputValidationError) as error:
        _assemble_numeric_draft(
            draft,
            bundle=bundle,
            allowed_evidence_refs={item.ref for item in bundle.items},
            value_catalog=_value_catalog(bundle),
            salvage=False,
            node="committee.final.serialize.numeric",
        )

    assert expected_issue in error.value.issue_codes
