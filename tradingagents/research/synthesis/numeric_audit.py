"""Validate and assemble evidence-backed numeric decision components."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from tradingagents.data.lookahead import is_near_live
from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    NumericAuditAppendixStatus,
    NumericAuditComponentType,
    NumericAuditStatus,
    NumericCalculationStatus,
    NumericDisplayStatus,
    ReportLanguage,
    ResearchScenarioKind,
)
from tradingagents.domain.decision import (
    AuditedRangeEndpoint,
    CalculationRecord,
    DecisionCalculationUse,
    EvidenceValueLocator,
    MarketReferenceLevel,
    NumericTemporalBasis,
    ScenarioReferenceRange,
    ValuationAssessment,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceTemporalScope,
    MeasurementKind,
)
from tradingagents.domain.instruments import market_timezone
from tradingagents.domain.numeric_audit import (
    DecisionNumericAuditAppendix,
    MarketReferenceBasis,
    NumericAuditOmission,
    NumericRequirementCheck,
)
from tradingagents.domain.reports import (
    ResearchWarning,
)
from tradingagents.research.synthesis.drafts import (
    CalculationRecordDraft,
    DecisionNumericDraft,
    DecisionNumericRequirementDraft,
    DerivedRangeEndpointDraft,
    EventWriter,
    InterpretedMarketReferenceLevelDraft,
    InterpretedRangeEndpointDraft,
    ObservedMarketReferenceLevelDraft,
    ObservedRangeEndpointDraft,
    RangeEndpointDraft,
    ScenarioReferenceRangeDraft,
)
from tradingagents.research.synthesis.numeric_evidence import (
    NumericValueCatalogEntry,
)
from tradingagents.research.synthesis.numeric_math import (
    _calculation_date_refs,
    _canonicalize_calculation_result,
    _display_values_approximately_match,
    _evaluate_formula,
    _formula_identity,
    _is_ratio_scaled_calculation_unit,
    _scale_for_display,
    requirement_input_mapping,
)
from tradingagents.research.synthesis.numeric_preflight import _NumericRequirementPreflight
from tradingagents.research.synthesis.output_validation import (
    OutputValidationError,
    require_nonempty_texts,
    require_text,
    require_valid_refs,
)

_SCENARIO_LABEL_PATTERNS: dict[
    ReportLanguage,
    dict[ResearchScenarioKind, tuple[str, ...]],
] = {
    ReportLanguage.ENGLISH: {
        ResearchScenarioKind.BASE: (r"\b(?:base|neutral)\s+(?:scenario|case)\b",),
        ResearchScenarioKind.BULL: (r"\b(?:bull|bullish|upside|recovery)\s+(?:scenario|case)\b",),
        ResearchScenarioKind.BEAR: (
            r"\b(?:bear|bearish|downside|deterioration)\s+(?:scenario|case)\b",
        ),
    },
    ReportLanguage.SIMPLIFIED_CHINESE: {
        ResearchScenarioKind.BASE: (r"(?:基准|中性)情景",),
        ResearchScenarioKind.BULL: (r"(?:乐观|上行|修复)情景",),
        ResearchScenarioKind.BEAR: (r"(?:悲观|下行|恶化)情景",),
    },
    ReportLanguage.JAPANESE: {
        ResearchScenarioKind.BASE: (r"(?:基準|中立)(?:シナリオ|ケース)",),
        ResearchScenarioKind.BULL: (r"(?:強気|上振れ|回復)(?:シナリオ|ケース)",),
        ResearchScenarioKind.BEAR: (r"(?:弱気|下振れ|悪化)(?:シナリオ|ケース)",),
    },
}


_FIAT_UNITS = {
    "AUD",
    "CAD",
    "CHF",
    "CNY",
    "EUR",
    "GBP",
    "HKD",
    "JPY",
    "KRW",
    "USD",
}


_VALUATION_LABEL_TOKENS = ("valuation", "估值", "バリュエーション", "企業価値")


def _label_declares_other_scenario(
    label: str,
    *,
    owner: ResearchScenarioKind,
    output_language: str,
) -> bool:
    language = next(
        (candidate for candidate in ReportLanguage if output_language == candidate.prompt_label),
        None,
    )
    if language is None:
        return False
    for scenario_kind, patterns in _SCENARIO_LABEL_PATTERNS[language].items():
        if scenario_kind is owner:
            continue
        if any(re.search(pattern, label, flags=re.IGNORECASE) for pattern in patterns):
            return True
    return False


def _valuation_label_requires_calculation(
    scenario: ScenarioReferenceRangeDraft,
) -> bool:
    if not any(token in scenario.label.casefold() for token in _VALUATION_LABEL_TOKENS):
        return False
    return any(
        not isinstance(endpoint, DerivedRangeEndpointDraft)
        for endpoint in (scenario.low, scenario.high)
    )


def _measurement_from_unit(unit: str | None) -> MeasurementKind:
    if unit is None:
        return MeasurementKind.UNKNOWN
    normalized = unit.strip().upper()
    if normalized in _FIAT_UNITS:
        return MeasurementKind.CURRENCY
    if normalized in {"%", "PCT", "PERCENT"}:
        return MeasurementKind.PERCENT
    if normalized in {"X", "倍"}:
        return MeasurementKind.RATIO
    return MeasurementKind.UNKNOWN


def _endpoint_measurement(
    endpoint: RangeEndpointDraft,
    *,
    value_catalog: Mapping[str, NumericValueCatalogEntry],
    calculations: Mapping[str, CalculationRecord],
    issue_prefix: str,
) -> tuple[MeasurementKind, str | None]:
    if isinstance(endpoint, ObservedRangeEndpointDraft):
        entry = value_catalog.get(endpoint.value_ref)
        if entry is None:
            return MeasurementKind.UNKNOWN, None
        return entry.measurement_kind, entry.unit
    if isinstance(endpoint, InterpretedRangeEndpointDraft):
        entries = tuple(value_catalog.get(ref) for ref in endpoint.anchor_value_refs)
        if any(entry is None for entry in entries):
            return MeasurementKind.UNKNOWN, None
        resolved = tuple(entry for entry in entries if entry is not None)
        if any(entry.measurement_kind is MeasurementKind.UNKNOWN for entry in resolved):
            return MeasurementKind.UNKNOWN, None
        measurements = {(entry.measurement_kind, entry.unit) for entry in resolved}
        if len(measurements) != 1:
            raise OutputValidationError(f"{issue_prefix}.measurement_mismatch")
        return next(iter(measurements))
    calculation = calculations.get(endpoint.calculation_id)
    if calculation is None:
        return MeasurementKind.UNKNOWN, None
    return _measurement_from_unit(calculation.unit), calculation.unit


def _range_measurement(
    scenario: ScenarioReferenceRangeDraft,
    *,
    value_catalog: Mapping[str, NumericValueCatalogEntry],
    calculations: Mapping[str, CalculationRecord],
    issue_prefix: str,
) -> tuple[MeasurementKind, str | None]:
    measurements = tuple(
        _endpoint_measurement(
            endpoint,
            value_catalog=value_catalog,
            calculations=calculations,
            issue_prefix=issue_prefix,
        )
        for endpoint in (scenario.low, scenario.high)
    )
    if any(kind is MeasurementKind.UNKNOWN for kind, _ in measurements):
        return MeasurementKind.UNKNOWN, None
    if len(set(measurements)) != 1:
        raise OutputValidationError(f"{issue_prefix}.measurement_mismatch")
    return measurements[0]


@dataclass(frozen=True)
class _NumericDecisionAssembly:
    scenario_reference_ranges: dict[ResearchScenarioKind, tuple[ScenarioReferenceRange, ...]]
    valuation_assessment: ValuationAssessment | None
    market_reference_levels: tuple[MarketReferenceLevel, ...]
    calculation_records: tuple[CalculationRecord, ...]
    status: NumericAuditStatus
    generation_method: ArtifactGenerationMethod = ArtifactGenerationMethod.TOOL_CALL
    warnings: tuple[ResearchWarning, ...] = ()
    issues: tuple[str, ...] = ()
    repair_issues: tuple[str, ...] = ()
    audit_issues: tuple[str, ...] = ()
    omissions: tuple[NumericAuditOmission, ...] = ()
    requirement_checks: tuple[NumericRequirementCheck, ...] = ()
    audit: DecisionNumericAuditAppendix | None = None
    promoted_singletons: int = 0
    reordered_ranges: int = 0


def _apply_requirement_preflight(
    assembly: _NumericDecisionAssembly,
    preflight: _NumericRequirementPreflight,
    *,
    node: str,
    event_writer: EventWriter | None,
) -> _NumericDecisionAssembly:
    if not preflight.issues:
        return assembly
    status = (
        NumericAuditStatus.INCOMPLETE
        if assembly.status is NumericAuditStatus.INCOMPLETE
        else NumericAuditStatus.PARTIAL
    )
    warning = ResearchWarning(
        code=f"decision.numeric_audit_{status.value}",
        message=(
            "Decision-critical numeric annotations were incomplete; the "
            "qualitative decision was retained and unverified calculations "
            "were omitted."
        ),
        source=node,
    )
    warnings = (
        assembly.warnings
        if any(item.code == warning.code for item in assembly.warnings)
        else (*assembly.warnings, warning)
    )
    omissions = tuple(dict.fromkeys((*assembly.omissions, *preflight.omissions)))
    appendix_status = (
        NumericAuditAppendixStatus.INCOMPLETE
        if status is NumericAuditStatus.INCOMPLETE
        else NumericAuditAppendixStatus.PARTIAL
    )
    audit = DecisionNumericAuditAppendix(
        status=appendix_status,
        requirement_checks=(
            assembly.audit.requirement_checks
            if assembly.audit is not None
            else assembly.requirement_checks
        ),
        snapshots=(assembly.audit.snapshots if assembly.audit is not None else ()),
        omitted_components=tuple(
            dict.fromkeys(
                (
                    *(assembly.audit.omitted_components if assembly.audit is not None else ()),
                    *omissions,
                )
            )
        ),
    )
    if event_writer is not None:
        event_writer(
            {
                "event_type": "node.numeric_audit_degraded",
                "node": node,
                "payload": {
                    "reason_code": "numeric_requirement_preflight",
                    "validation_issues": list(preflight.issues),
                },
            }
        )
    return replace(
        assembly,
        status=status,
        warnings=warnings,
        issues=tuple(dict.fromkeys((*assembly.issues, *preflight.issues))),
        omissions=omissions,
        audit=audit,
    )


def _requirement_omissions(
    requirements: tuple[DecisionNumericRequirementDraft, ...],
    *,
    issue_suffix: str,
) -> tuple[NumericAuditOmission, ...]:
    return tuple(
        NumericAuditOmission(
            component_path=requirement.component_path,
            component_type=NumericAuditComponentType.DECISION_CLAIM,
            reference_label=requirement.label,
            issue_codes=(f"numeric.requirement.{requirement.id}.{issue_suffix}",),
        )
        for requirement in requirements
    )


def _empty_numeric_assembly(
    *,
    node: str,
    status: NumericAuditStatus,
    requirement_checks: tuple[NumericRequirementCheck, ...] = (),
) -> _NumericDecisionAssembly:
    return _NumericDecisionAssembly(
        scenario_reference_ranges={},
        valuation_assessment=None,
        market_reference_levels=(),
        calculation_records=(),
        status=status,
        requirement_checks=requirement_checks,
        warnings=(
            ResearchWarning(
                code=f"decision.numeric_audit_{status.value}",
                message=(
                    "Optional valuation and market-reference figures were "
                    "omitted because their calculations could not be fully "
                    "validated. The qualitative decision remains audited."
                ),
                source=node,
            ),
        ),
    )


def _same_numeric_endpoint_identity(
    low: RangeEndpointDraft,
    high: RangeEndpointDraft,
) -> bool:
    if type(low) is not type(high):
        return False
    if isinstance(low, ObservedRangeEndpointDraft) and isinstance(high, ObservedRangeEndpointDraft):
        return low.value_ref == high.value_ref
    if isinstance(low, InterpretedRangeEndpointDraft) and isinstance(
        high, InterpretedRangeEndpointDraft
    ):
        return (
            low.value == high.value
            and set(low.anchor_value_refs) == set(high.anchor_value_refs)
            and set(low.context_evidence_refs) == set(high.context_evidence_refs)
        )
    if isinstance(low, DerivedRangeEndpointDraft) and isinstance(high, DerivedRangeEndpointDraft):
        return low.calculation_id == high.calculation_id
    return False


def _market_reference_identity(level: MarketReferenceLevel) -> str:
    if level.basis is MarketReferenceBasis.OBSERVED:
        payload: Any = (
            level.source_locator.model_dump(mode="json")
            if level.source_locator is not None
            else None
        )
    elif level.basis is MarketReferenceBasis.INTERPRETED:
        payload = {
            "value": level.value,
            "evidence_refs": sorted(level.evidence_refs),
            "date_evidence_refs": sorted(level.date_evidence_refs),
        }
    else:
        payload = sorted(level.calculation_ids)
    return json.dumps(
        {"basis": level.basis.value, "identity": payload},
        sort_keys=True,
        separators=(",", ":"),
    )


def _requirement_check(
    requirement: DecisionNumericRequirementDraft,
    *,
    calculation_status: NumericCalculationStatus,
    display_status: NumericDisplayStatus,
    calculation_id: str | None = None,
    canonical_result: int | float | None = None,
    comparison_result: int | float | None = None,
    comparison_difference: int | float | None = None,
    rounded_stated_value: int | float | None = None,
    rounded_canonical_result: int | float | None = None,
    issue_codes: tuple[str, ...] = (),
) -> NumericRequirementCheck:
    input_evidence_refs = requirement.input_evidence_refs
    input_evidence_ref_set = set(input_evidence_refs)
    date_evidence_refs = _calculation_date_refs(requirement.inputs)
    invalid_date_refs = not set(date_evidence_refs).issubset(input_evidence_ref_set)
    if invalid_date_refs:
        date_evidence_refs = tuple(
            ref for ref in date_evidence_refs if ref in input_evidence_ref_set
        )
        calculation_status = NumericCalculationStatus.INVALID
        display_status = NumericDisplayStatus.NOT_CHECKED
        calculation_id = None
        canonical_result = None
        comparison_result = None
        comparison_difference = None
        rounded_stated_value = None
        rounded_canonical_result = None
        issue_codes = tuple(
            dict.fromkeys(
                (
                    *issue_codes,
                    f"numeric.requirement.{requirement.id}.date_refs.not_input_refs",
                )
            )
        )
    return NumericRequirementCheck(
        requirement_id=requirement.id,
        calculation_id=calculation_id,
        component_path=requirement.component_path,
        label=requirement.label,
        stated_value=requirement.stated_value,
        fraction_digits=requirement.fraction_digits,
        unit=requirement.unit,
        display_scale=requirement.display_scale,
        formula=requirement.formula,
        inputs=requirement_input_mapping(requirement),
        input_evidence_refs=input_evidence_refs,
        date_evidence_refs=date_evidence_refs,
        canonical_result=canonical_result,
        comparison_result=comparison_result,
        comparison_difference=comparison_difference,
        rounded_stated_value=rounded_stated_value,
        rounded_canonical_result=rounded_canonical_result,
        calculation_status=calculation_status,
        display_status=display_status,
        issue_codes=issue_codes,
    )


def _missing_requirement_checks(
    requirements: tuple[DecisionNumericRequirementDraft, ...],
    *,
    issue_suffix: str,
) -> tuple[NumericRequirementCheck, ...]:
    return tuple(
        _requirement_check(
            requirement,
            calculation_status=NumericCalculationStatus.MISSING,
            display_status=NumericDisplayStatus.NOT_CHECKED,
            issue_codes=(f"numeric.requirement.{requirement.id}.{issue_suffix}",),
        )
        for requirement in requirements
    )


def _assemble_numeric_draft(
    draft: DecisionNumericDraft,
    *,
    bundle: EvidenceBundle,
    allowed_evidence_refs: set[str],
    value_catalog: Mapping[str, NumericValueCatalogEntry],
    salvage: bool,
    node: str,
    output_language: str = ReportLanguage.ENGLISH.prompt_label,
    requirements: tuple[DecisionNumericRequirementDraft, ...] = (),
) -> _NumericDecisionAssembly:
    repair_issues: list[str] = []
    audit_issues: list[str] = []
    calculations: dict[str, CalculationRecord] = {}
    calculation_drafts: dict[str, CalculationRecordDraft] = {}
    raw_calculation_results: dict[str, float] = {}
    requirement_by_id = {item.id: item for item in requirements}
    requirement_checks: dict[str, NumericRequirementCheck] = {}
    evidence_items = {item.ref: item for item in bundle.items}
    duplicate_ids = {
        item.id
        for item in draft.calculation_records
        if sum(other.id == item.id for other in draft.calculation_records) > 1
    }
    for item in draft.calculation_records:
        prefix = f"numeric.calculation.{item.id}"
        if item.id in duplicate_ids:
            repair_issues.append(f"{prefix}.duplicate_id")
            continue
        try:
            require_nonempty_texts(item.limitations)
            require_valid_refs(
                item.input_evidence_refs,
                allowed_evidence_refs,
                required=True,
            )
            date_evidence_refs = _calculation_date_refs(item.inputs)
            require_valid_refs(
                date_evidence_refs,
                allowed_evidence_refs,
                required=False,
            )
            if not set(date_evidence_refs).issubset(item.input_evidence_refs):
                raise OutputValidationError(f"{prefix}.date_refs.not_input_refs")
            inputs = item.input_mapping()
            raw_calculated = _evaluate_formula(
                item.formula,
                inputs,
                issue_prefix=prefix,
            )
            calculated = _canonicalize_calculation_result(
                raw_calculated,
                item.unit,
                issue_prefix=prefix,
            )
            resolved_date = _latest_evidence_date(
                date_evidence_refs or item.input_evidence_refs,
                evidence_items=evidence_items,
                bundle=bundle,
                issue_prefix=prefix,
            )
            calculations[item.id] = CalculationRecord(
                id=item.id,
                formula=item.formula,
                inputs=inputs,
                input_evidence_refs=item.input_evidence_refs,
                date_evidence_refs=date_evidence_refs or item.input_evidence_refs,
                result=calculated,
                unit=item.unit,
                as_of_date=resolved_date.value,
                temporal_basis=resolved_date.temporal_basis,
                limitations=item.limitations,
            )
            calculation_drafts[item.id] = item
            raw_calculation_results[item.id] = raw_calculated
        except OutputValidationError as exc:
            repair_issues.append(exc.issue_code)
            for requirement_id in item.requirement_ids:
                requirement = requirement_by_id.get(requirement_id)
                if requirement is not None:
                    requirement_checks[requirement_id] = _requirement_check(
                        requirement,
                        calculation_id=item.id,
                        calculation_status=NumericCalculationStatus.INVALID,
                        display_status=NumericDisplayStatus.NOT_CHECKED,
                        issue_codes=(exc.issue_code,),
                    )

    requirement_uses: dict[str, list[DecisionCalculationUse]] = {}
    covered_requirements: set[str] = set()
    requirement_calculations: dict[str, list[str]] = {}
    for calculation_id, item in calculation_drafts.items():
        for requirement_id in item.requirement_ids:
            requirement_calculations.setdefault(requirement_id, []).append(calculation_id)
    multiply_covered_requirements = {
        requirement_id
        for requirement_id, calculation_ids in requirement_calculations.items()
        if len(calculation_ids) > 1
    }
    for requirement_id in sorted(multiply_covered_requirements):
        requirement = requirement_by_id.get(requirement_id)
        if requirement is None:
            continue
        issue = f"numeric.requirement.{requirement_id}.multiple_calculations"
        repair_issues.append(issue)
        requirement_checks[requirement_id] = _requirement_check(
            requirement,
            calculation_status=NumericCalculationStatus.INVALID,
            display_status=NumericDisplayStatus.NOT_CHECKED,
            issue_codes=(issue,),
        )
    for calculation_id, item in calculation_drafts.items():
        for requirement_id in item.requirement_ids:
            requirement = requirement_by_id.get(requirement_id)
            if requirement is None:
                repair_issues.append(f"numeric.calculation.{calculation_id}.unknown_requirement")
                continue
            if requirement_id in multiply_covered_requirements:
                continue
            prefix = f"numeric.requirement.{requirement_id}"
            mismatch: str | None = None
            canonical_value: Decimal | None = None
            stated_value: Decimal | None = None
            if _formula_identity(item.formula) != _formula_identity(requirement.formula):
                mismatch = "formula_mismatch"
            elif item.input_mapping() != requirement_input_mapping(requirement):
                mismatch = "inputs_mismatch"
            elif _calculation_date_refs(item.inputs) != _calculation_date_refs(requirement.inputs):
                mismatch = "date_evidence_mismatch"
            elif set(item.input_evidence_refs) != set(requirement.input_evidence_refs):
                mismatch = "evidence_mismatch"
            elif item.unit != requirement.unit:
                mismatch = "unit_mismatch"
            else:
                quantum = Decimal(1).scaleb(-requirement.fraction_digits)
                comparison_result = _scale_for_display(
                    calculations[calculation_id].result,
                    requirement.display_scale,
                )
                canonical_value = Decimal(str(comparison_result)).quantize(
                    quantum,
                    rounding=ROUND_HALF_UP,
                )
                stated_value = Decimal(str(requirement.stated_value)).quantize(
                    quantum,
                    rounding=ROUND_HALF_UP,
                )
                if canonical_value != stated_value:
                    raw_result = Decimal(str(raw_calculation_results[calculation_id])).quantize(
                        quantum,
                        rounding=ROUND_HALF_UP,
                    )
                    if _is_ratio_scaled_calculation_unit(item.unit) and raw_result == stated_value:
                        mismatch = "percent_scale_mismatch"
                    elif _display_values_approximately_match(
                        stated_value=stated_value,
                        comparison_value=canonical_value,
                        raw_stated_value=Decimal(str(requirement.stated_value)),
                        raw_comparison_value=Decimal(str(comparison_result)),
                        quantum=quantum,
                    ):
                        covered_requirements.add(requirement_id)
                        requirement_uses.setdefault(calculation_id, []).append(
                            DecisionCalculationUse(
                                component_path=requirement.component_path,
                                label=requirement.label,
                            )
                        )
                        requirement_checks[requirement_id] = _requirement_check(
                            requirement,
                            calculation_id=calculation_id,
                            canonical_result=calculations[calculation_id].result,
                            comparison_result=comparison_result,
                            comparison_difference=(comparison_result - requirement.stated_value),
                            rounded_stated_value=float(stated_value),
                            rounded_canonical_result=float(canonical_value),
                            calculation_status=NumericCalculationStatus.VERIFIED,
                            display_status=NumericDisplayStatus.APPROXIMATELY_MATCHED,
                            issue_codes=(f"{prefix}.display_approximate",),
                        )
                        continue
                    else:
                        mismatch = "result_mismatch"
            if mismatch is not None:
                issue = f"{prefix}.{mismatch}"
                if mismatch == "result_mismatch":
                    audit_issues.append(issue)
                    covered_requirements.add(requirement_id)
                    requirement_uses.setdefault(calculation_id, []).append(
                        DecisionCalculationUse(
                            component_path=requirement.component_path,
                            label=requirement.label,
                        )
                    )
                    requirement_checks[requirement_id] = _requirement_check(
                        requirement,
                        calculation_id=calculation_id,
                        canonical_result=calculations[calculation_id].result,
                        comparison_result=comparison_result,
                        comparison_difference=(comparison_result - requirement.stated_value),
                        rounded_stated_value=float(stated_value),
                        rounded_canonical_result=float(canonical_value),
                        calculation_status=NumericCalculationStatus.VERIFIED,
                        display_status=NumericDisplayStatus.MISMATCHED,
                        issue_codes=(issue,),
                    )
                    continue
                repair_issues.append(issue)
                requirement_checks[requirement_id] = _requirement_check(
                    requirement,
                    calculation_id=calculation_id,
                    calculation_status=NumericCalculationStatus.INVALID,
                    display_status=NumericDisplayStatus.NOT_CHECKED,
                    issue_codes=(issue,),
                )
                continue
            covered_requirements.add(requirement_id)
            requirement_uses.setdefault(calculation_id, []).append(
                DecisionCalculationUse(
                    component_path=requirement.component_path,
                    label=requirement.label,
                )
            )
            requirement_checks[requirement_id] = _requirement_check(
                requirement,
                calculation_id=calculation_id,
                canonical_result=calculations[calculation_id].result,
                comparison_result=comparison_result,
                comparison_difference=(comparison_result - requirement.stated_value),
                rounded_stated_value=float(stated_value),
                rounded_canonical_result=float(canonical_value),
                calculation_status=NumericCalculationStatus.VERIFIED,
                display_status=NumericDisplayStatus.MATCHED,
            )

    for requirement in requirements:
        if requirement.id not in covered_requirements:
            issue = f"numeric.requirement.{requirement.id}.missing_calculation"
            existing_issues = (*repair_issues, *audit_issues)
            if not any(
                item.startswith(f"numeric.requirement.{requirement.id}.")
                for item in existing_issues
            ):
                repair_issues.append(issue)
            if requirement.id not in requirement_checks:
                requirement_checks[requirement.id] = _requirement_check(
                    requirement,
                    calculation_status=NumericCalculationStatus.MISSING,
                    display_status=NumericDisplayStatus.NOT_CHECKED,
                    issue_codes=(issue,),
                )

    calculations = {
        calculation_id: calculation.model_copy(
            update={"decision_uses": tuple(requirement_uses.get(calculation_id, ()))},
        )
        for calculation_id, calculation in calculations.items()
    }

    scenario_values: dict[ResearchScenarioKind, tuple[ScenarioReferenceRange, ...]] = {}
    duplicate_warnings: list[ResearchWarning] = []
    promoted_references: list[MarketReferenceLevel] = []
    reordered_ranges = 0
    linked_ids: set[str] = set(requirement_uses)
    for scenario_kind, scenario_ranges in draft.scenario_reference_ranges.items():
        assembled_ranges: list[ScenarioReferenceRange] = []
        seen_range_keys: set[str] = set()
        duplicate_ranges_removed = 0
        for index, scenario in enumerate(scenario_ranges):
            range_key = json.dumps(
                scenario.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if range_key in seen_range_keys:
                duplicate_ranges_removed += 1
                continue
            seen_range_keys.add(range_key)
            prefix = f"numeric.scenario.{scenario_kind.value}.ranges.{index}"
            if _label_declares_other_scenario(
                scenario.label,
                owner=scenario_kind,
                output_language=output_language,
            ):
                repair_issues.append(f"{prefix}.scenario_mismatch")
                continue
            if _valuation_label_requires_calculation(scenario):
                repair_issues.append(f"{prefix}.derived_calculation_required")
                continue
            try:
                require_text(scenario.label)
                require_text(scenario.interpretation)
                require_nonempty_texts(scenario.limitations)
            except OutputValidationError as exc:
                repair_issues.append(f"{prefix}.{exc.issue_code}")
                continue
            endpoints: dict[str, AuditedRangeEndpoint] = {}
            for endpoint_name, endpoint_draft in (
                ("low", scenario.low),
                ("high", scenario.high),
            ):
                try:
                    endpoints[endpoint_name] = _assemble_range_endpoint(
                        endpoint_draft,
                        calculations=calculations,
                        evidence_items=evidence_items,
                        bundle=bundle,
                        allowed_evidence_refs=allowed_evidence_refs,
                        value_catalog=value_catalog,
                        issue_prefix=f"{prefix}.{endpoint_name}",
                    )
                except OutputValidationError as exc:
                    repair_issues.append(exc.issue_code)
            if set(endpoints) != {"low", "high"}:
                continue
            try:
                measurement_kind, unit = _range_measurement(
                    scenario,
                    value_catalog=value_catalog,
                    calculations=calculations,
                    issue_prefix=prefix,
                )
            except OutputValidationError as exc:
                repair_issues.append(exc.issue_code)
                continue
            if endpoints["high"].value == endpoints["low"].value:
                if _same_numeric_endpoint_identity(scenario.low, scenario.high):
                    endpoint = endpoints["low"]
                    promoted_references.append(
                        MarketReferenceLevel(
                            label=scenario.label,
                            value=endpoint.value,
                            measurement_kind=measurement_kind,
                            unit=unit,
                            as_of_date=endpoint.as_of_date,
                            interpretation=scenario.interpretation,
                            evidence_refs=endpoint.evidence_refs,
                            date_evidence_refs=endpoint.date_evidence_refs,
                            basis=endpoint.basis,
                            source_locator=endpoint.source_locator,
                            calculation_ids=(
                                (endpoint.calculation_id,)
                                if endpoint.calculation_id is not None
                                else ()
                            ),
                            temporal_basis=endpoint.temporal_basis,
                        )
                    )
                    if endpoint.calculation_id is not None:
                        linked_ids.add(endpoint.calculation_id)
                    continue
                repair_issues.append(f"{prefix}.invalid_range")
                continue
            if endpoints["high"].value < endpoints["low"].value:
                endpoints["low"], endpoints["high"] = (
                    endpoints["high"],
                    endpoints["low"],
                )
                reordered_ranges += 1
            assembled_ranges.append(
                ScenarioReferenceRange(
                    category=scenario.category,
                    label=scenario.label,
                    low=endpoints["low"],
                    high=endpoints["high"],
                    measurement_kind=measurement_kind,
                    unit=unit,
                    interpretation=scenario.interpretation,
                    limitations=scenario.limitations,
                )
            )
            linked_ids.update(
                endpoint.calculation_id
                for endpoint in endpoints.values()
                if endpoint.calculation_id is not None
            )
        if assembled_ranges:
            scenario_values[scenario_kind] = tuple(assembled_ranges)
        if duplicate_ranges_removed:
            duplicate_warnings.append(
                ResearchWarning(
                    code="decision.numeric_duplicate_removed",
                    message=(
                        f"Removed {duplicate_ranges_removed} exact duplicate "
                        f"reference range(s) from the {scenario_kind.value} scenario."
                    ),
                    source=node,
                )
            )

    valuation: ValuationAssessment | None = None
    if draft.valuation_assessment is not None:
        item = draft.valuation_assessment
        prefix = "numeric.valuation"
        try:
            require_text(item.method)
            require_nonempty_texts(item.limitations)
            low = _assemble_range_endpoint(
                item.low,
                calculations=calculations,
                evidence_items=evidence_items,
                bundle=bundle,
                allowed_evidence_refs=allowed_evidence_refs,
                value_catalog=value_catalog,
                issue_prefix=f"{prefix}.low",
            )
            high = _assemble_range_endpoint(
                item.high,
                calculations=calculations,
                evidence_items=evidence_items,
                bundle=bundle,
                allowed_evidence_refs=allowed_evidence_refs,
                value_catalog=value_catalog,
                issue_prefix=f"{prefix}.high",
            )
        except OutputValidationError as exc:
            repair_issues.append(exc.issue_code)
        else:
            if high.value < low.value:
                low, high = high, low
                reordered_ranges += 1
            low_measurement = _endpoint_measurement(
                item.low,
                value_catalog=value_catalog,
                calculations=calculations,
                issue_prefix=f"{prefix}.low",
            )
            high_measurement = _endpoint_measurement(
                item.high,
                value_catalog=value_catalog,
                calculations=calculations,
                issue_prefix=f"{prefix}.high",
            )
            if (
                low_measurement[0] is MeasurementKind.UNKNOWN
                or low_measurement[1] is None
                or low_measurement != high_measurement
            ):
                repair_issues.append(f"{prefix}.measurement_mismatch")
            else:
                valuation = ValuationAssessment(
                    method=item.method,
                    low=low,
                    high=high,
                    measurement_kind=low_measurement[0],
                    unit=low_measurement[1],
                    limitations=item.limitations,
                )
                linked_ids.update(valuation.calculation_ids)

    reference_levels: list[MarketReferenceLevel] = []
    for index, item in enumerate(draft.market_reference_levels):
        prefix = f"numeric.market_reference.{index}"
        try:
            require_text(item.interpretation)
            if isinstance(item, ObservedMarketReferenceLevelDraft):
                catalog_entry = value_catalog.get(item.value_ref)
                if catalog_entry is None:
                    raise OutputValidationError(f"{prefix}.unknown_observed_value")
                resolved_date = _catalog_entry_date(
                    catalog_entry,
                    evidence_items=evidence_items,
                    bundle=bundle,
                    issue_prefix=prefix,
                )
                as_of_date = resolved_date.value
                temporal_basis = resolved_date.temporal_basis
                value = catalog_entry.value
                measurement_kind = catalog_entry.measurement_kind
                unit = catalog_entry.unit
                evidence_refs = catalog_entry.evidence_refs
                source_locator: EvidenceValueLocator | None = catalog_entry.locator
                calculation_ids: tuple[str, ...] = ()
            elif isinstance(item, InterpretedMarketReferenceLevelDraft):
                anchor_entries = _numeric_anchor_entries(
                    item.anchor_value_refs,
                    value_catalog=value_catalog,
                    issue_prefix=prefix,
                )
                require_valid_refs(
                    item.context_evidence_refs,
                    allowed_evidence_refs,
                    required=False,
                )
                resolved_date = _latest_catalog_date(
                    anchor_entries,
                    evidence_items=evidence_items,
                    bundle=bundle,
                    issue_prefix=prefix,
                )
                as_of_date = resolved_date.value
                temporal_basis = resolved_date.temporal_basis
                value = item.value
                if any(
                    entry.measurement_kind is MeasurementKind.UNKNOWN for entry in anchor_entries
                ):
                    measurement_kind = MeasurementKind.UNKNOWN
                    unit = None
                else:
                    measurements = {
                        (entry.measurement_kind, entry.unit) for entry in anchor_entries
                    }
                    if len(measurements) != 1:
                        raise OutputValidationError(f"{prefix}.measurement_mismatch")
                    measurement_kind, unit = next(iter(measurements))
                date_evidence_refs = _catalog_evidence_refs(anchor_entries)
                evidence_refs = tuple(
                    dict.fromkeys((*date_evidence_refs, *item.context_evidence_refs))
                )
                source_locator = None
                calculation_ids: tuple[str, ...] = ()
            else:
                calculation = calculations.get(item.calculation_id)
                if calculation is None:
                    raise OutputValidationError(f"{prefix}.unknown_calculation")
                as_of_date = calculation.as_of_date
                value = float(calculation.result)
                measurement_kind = _measurement_from_unit(calculation.unit)
                unit = calculation.unit
                evidence_refs = calculation.input_evidence_refs
                date_evidence_refs = calculation.date_evidence_refs
                source_locator = None
                calculation_ids = (item.calculation_id,)
                temporal_basis = calculation.temporal_basis
            if isinstance(item, ObservedMarketReferenceLevelDraft):
                date_evidence_refs = catalog_entry.evidence_refs
        except OutputValidationError as exc:
            repair_issues.append(exc.issue_code)
        else:
            reference_levels.append(
                MarketReferenceLevel(
                    label=item.label,
                    value=value,
                    measurement_kind=measurement_kind,
                    unit=unit,
                    as_of_date=as_of_date,
                    interpretation=item.interpretation,
                    evidence_refs=evidence_refs,
                    date_evidence_refs=date_evidence_refs,
                    basis=item.basis,
                    source_locator=source_locator,
                    calculation_ids=calculation_ids,
                    temporal_basis=temporal_basis,
                )
            )
            linked_ids.update(calculation_ids)

    explicit_references = {_market_reference_identity(level) for level in reference_levels}
    for promoted in promoted_references:
        identity = _market_reference_identity(promoted)
        if identity in explicit_references:
            continue
        reference_levels.append(promoted)
        explicit_references.add(identity)

    orphaned = set(calculations).difference(linked_ids)
    for calculation_id in sorted(orphaned):
        repair_issues.append(f"numeric.calculation.{calculation_id}.orphaned")
    if draft.requested and not (
        scenario_values or valuation is not None or reference_levels or linked_ids
    ):
        repair_issues.append("numeric.requested.empty")
    if not draft.requested and (
        draft.scenario_reference_ranges.has_content()
        or draft.valuation_assessment is not None
        or draft.market_reference_levels
        or draft.calculation_records
    ):
        repair_issues.append("numeric.not_requested.has_content")

    if repair_issues and not salvage:
        raise OutputValidationError(
            repair_issues[0],
            issue_codes=tuple(repair_issues),
        )

    kept_calculations = tuple(
        calculation
        for calculation_id, calculation in calculations.items()
        if calculation_id in linked_ids
    )
    has_content = bool(
        scenario_values or valuation is not None or reference_levels or kept_calculations
    )
    omissions = _numeric_omissions(
        draft,
        tuple(repair_issues),
        requirements=requirements,
    )
    all_issues = tuple(dict.fromkeys((*repair_issues, *audit_issues)))
    if all_issues:
        status = (
            NumericAuditStatus.PARTIAL
            if has_content or requirements
            else NumericAuditStatus.INCOMPLETE
        )
        omitted = ", ".join(item.reference_label or item.component_path for item in omissions)
        warning = (
            ResearchWarning(
                code="decision.numeric_display_mismatch",
                message=(
                    "A decision-critical calculation was valid, but its canonical "
                    "result did not match the value stated in the decision text."
                ),
                source=node,
            )
            if audit_issues and not repair_issues
            else ResearchWarning(
                code=f"decision.numeric_audit_{status.value}",
                message=(
                    "Optional numeric components were omitted because their "
                    "audit failed"
                    + (f": {omitted}." if omitted else ".")
                    + " The qualitative decision remains audited."
                ),
                source=node,
            )
        )
        warnings = (warning, *duplicate_warnings)
    else:
        status = NumericAuditStatus.COMPLETE if has_content else NumericAuditStatus.NOT_APPLICABLE
        warnings = tuple(duplicate_warnings)
    appendix_status = (
        NumericAuditAppendixStatus.COMPLETE
        if status is NumericAuditStatus.COMPLETE
        else NumericAuditAppendixStatus.PARTIAL
        if status is NumericAuditStatus.PARTIAL
        else NumericAuditAppendixStatus.INCOMPLETE
    )
    checks = tuple(
        requirement_checks[item.id] for item in requirements if item.id in requirement_checks
    )
    return _NumericDecisionAssembly(
        scenario_reference_ranges=scenario_values,
        valuation_assessment=valuation,
        market_reference_levels=tuple(reference_levels),
        calculation_records=kept_calculations,
        status=status,
        warnings=warnings,
        issues=all_issues,
        repair_issues=tuple(repair_issues),
        audit_issues=tuple(audit_issues),
        omissions=omissions,
        requirement_checks=checks,
        audit=(
            DecisionNumericAuditAppendix(
                status=appendix_status,
                requirement_checks=checks,
                snapshots=(),
                omitted_components=omissions,
            )
            if requirements
            else None
        ),
        promoted_singletons=len(promoted_references),
        reordered_ranges=reordered_ranges,
    )


def _numeric_omissions(
    draft: DecisionNumericDraft,
    issues: tuple[str, ...],
    *,
    requirements: tuple[DecisionNumericRequirementDraft, ...] = (),
) -> tuple[NumericAuditOmission, ...]:
    grouped: dict[
        tuple[
            str,
            NumericAuditComponentType,
            ResearchScenarioKind | None,
            str | None,
        ],
        list[str],
    ] = {}
    reference_labels = {
        str(index): item.label for index, item in enumerate(draft.market_reference_levels)
    }
    scenario_labels = {
        (kind.value, str(index)): item.label
        for kind, ranges in draft.scenario_reference_ranges.items()
        for index, item in enumerate(ranges)
    }
    requirement_labels = {item.id: (item.component_path, item.label) for item in requirements}
    for issue in issues:
        parts = issue.split(".")
        path = "numeric.appendix"
        component_type = NumericAuditComponentType.APPENDIX
        scenario_kind: ResearchScenarioKind | None = None
        reference_label: str | None = None
        if len(parts) >= 4 and parts[:2] == ["numeric", "calculation"]:
            path = ".".join(parts[:3])
            component_type = NumericAuditComponentType.CALCULATION
            reference_label = parts[2]
        elif len(parts) >= 6 and parts[:2] == ["numeric", "scenario"] and parts[3] == "ranges":
            path = ".".join(parts[:5])
            component_type = NumericAuditComponentType.SCENARIO_RANGE
            try:
                scenario_kind = ResearchScenarioKind(parts[2])
            except ValueError:
                scenario_kind = None
            reference_label = scenario_labels.get((parts[2], parts[4]))
        elif parts[:2] == ["numeric", "valuation"]:
            path = "numeric.valuation"
            component_type = NumericAuditComponentType.VALUATION
        elif len(parts) >= 4 and parts[:2] == ["numeric", "market_reference"]:
            path = ".".join(parts[:3])
            component_type = NumericAuditComponentType.MARKET_REFERENCE
            reference_label = reference_labels.get(parts[2])
        elif len(parts) >= 4 and parts[:2] == ["numeric", "requirement"]:
            component_type = NumericAuditComponentType.DECISION_CLAIM
            requirement_path, requirement_label = requirement_labels.get(
                parts[2],
                (f"numeric.requirement.{parts[2]}", parts[2]),
            )
            path = requirement_path
            reference_label = requirement_label
        grouped.setdefault((path, component_type, scenario_kind, reference_label), []).append(issue)
    return tuple(
        NumericAuditOmission(
            component_path=path,
            component_type=component_type,
            scenario_kind=scenario_kind,
            reference_label=reference_label,
            issue_codes=tuple(dict.fromkeys(component_issues)),
        )
        for (
            path,
            component_type,
            scenario_kind,
            reference_label,
        ), component_issues in grouped.items()
    )


def _assemble_range_endpoint(
    draft: RangeEndpointDraft,
    *,
    calculations: Mapping[str, CalculationRecord],
    evidence_items: Mapping[str, EvidenceItem],
    bundle: EvidenceBundle,
    allowed_evidence_refs: set[str],
    value_catalog: Mapping[str, NumericValueCatalogEntry],
    issue_prefix: str,
) -> AuditedRangeEndpoint:
    if isinstance(draft, ObservedRangeEndpointDraft):
        catalog_entry = value_catalog.get(draft.value_ref)
        if catalog_entry is None:
            raise OutputValidationError(f"{issue_prefix}.unknown_observed_value")
        resolved_date = _catalog_entry_date(
            catalog_entry,
            evidence_items=evidence_items,
            bundle=bundle,
            issue_prefix=issue_prefix,
        )
        return AuditedRangeEndpoint(
            value=catalog_entry.value,
            basis=MarketReferenceBasis.OBSERVED,
            evidence_refs=catalog_entry.evidence_refs,
            date_evidence_refs=catalog_entry.evidence_refs,
            source_locator=catalog_entry.locator,
            as_of_date=resolved_date.value,
            temporal_basis=resolved_date.temporal_basis,
        )
    if isinstance(draft, InterpretedRangeEndpointDraft):
        anchor_entries = _numeric_anchor_entries(
            draft.anchor_value_refs,
            value_catalog=value_catalog,
            issue_prefix=issue_prefix,
        )
        try:
            require_valid_refs(
                draft.context_evidence_refs,
                allowed_evidence_refs,
                required=False,
            )
        except OutputValidationError as exc:
            raise OutputValidationError(f"{issue_prefix}.invalid_evidence") from exc
        resolved_date = _latest_catalog_date(
            anchor_entries,
            evidence_items=evidence_items,
            bundle=bundle,
            issue_prefix=issue_prefix,
        )
        date_evidence_refs = _catalog_evidence_refs(anchor_entries)
        evidence_refs = tuple(dict.fromkeys((*date_evidence_refs, *draft.context_evidence_refs)))
        return AuditedRangeEndpoint(
            value=draft.value,
            basis=MarketReferenceBasis.INTERPRETED,
            evidence_refs=evidence_refs,
            date_evidence_refs=date_evidence_refs,
            as_of_date=resolved_date.value,
            temporal_basis=resolved_date.temporal_basis,
        )
    calculation = calculations.get(draft.calculation_id)
    if calculation is None:
        raise OutputValidationError(f"{issue_prefix}.unknown_calculation")
    return AuditedRangeEndpoint(
        value=float(calculation.result),
        basis=MarketReferenceBasis.DERIVED,
        evidence_refs=calculation.input_evidence_refs,
        date_evidence_refs=calculation.date_evidence_refs,
        calculation_id=calculation.id,
        as_of_date=calculation.as_of_date,
        temporal_basis=calculation.temporal_basis,
    )


def _catalog_entry_date(
    entry: NumericValueCatalogEntry,
    *,
    evidence_items: Mapping[str, EvidenceItem],
    bundle: EvidenceBundle,
    issue_prefix: str,
) -> _ResolvedEvidenceDate:
    if entry.observed_date is not None:
        if entry.observed_date > bundle.analysis_date:
            raise OutputValidationError(f"{issue_prefix}.future_date")
        return _ResolvedEvidenceDate(
            value=entry.observed_date,
            temporal_basis=NumericTemporalBasis.POINT_IN_TIME,
        )
    return _latest_evidence_date(
        entry.evidence_refs,
        evidence_items=evidence_items,
        bundle=bundle,
        issue_prefix=issue_prefix,
    )


def _numeric_anchor_entries(
    anchor_value_refs: tuple[str, ...],
    *,
    value_catalog: Mapping[str, NumericValueCatalogEntry],
    issue_prefix: str,
) -> tuple[NumericValueCatalogEntry, ...]:
    entries = tuple(value_catalog.get(item) for item in anchor_value_refs)
    if not entries or any(item is None for item in entries):
        raise OutputValidationError(f"{issue_prefix}.anchor_unavailable")
    return tuple(item for item in entries if item is not None)


def _catalog_evidence_refs(
    entries: tuple[NumericValueCatalogEntry, ...],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ref for entry in entries for ref in entry.evidence_refs))


def _latest_catalog_date(
    entries: tuple[NumericValueCatalogEntry, ...],
    *,
    evidence_items: Mapping[str, EvidenceItem],
    bundle: EvidenceBundle,
    issue_prefix: str,
) -> _ResolvedEvidenceDate:
    resolved = tuple(
        _catalog_entry_date(
            entry,
            evidence_items=evidence_items,
            bundle=bundle,
            issue_prefix=issue_prefix,
        )
        for entry in entries
    )
    if not resolved:
        raise OutputValidationError(f"{issue_prefix}.anchor_unavailable")
    return _ResolvedEvidenceDate(
        value=max(item.value for item in resolved),
        temporal_basis=(
            NumericTemporalBasis.LIVE_SNAPSHOT
            if any(item.temporal_basis is NumericTemporalBasis.LIVE_SNAPSHOT for item in resolved)
            else NumericTemporalBasis.POINT_IN_TIME
        ),
    )


@dataclass(frozen=True)
class _ResolvedEvidenceDate:
    value: date
    temporal_basis: NumericTemporalBasis


def _latest_evidence_date(
    evidence_refs: tuple[str, ...],
    *,
    evidence_items: Mapping[str, EvidenceItem],
    bundle: EvidenceBundle,
    issue_prefix: str,
) -> _ResolvedEvidenceDate:
    dates: list[date] = []
    has_live_snapshot = False
    for evidence_ref in evidence_refs:
        item = evidence_items.get(evidence_ref)
        if item is None:
            raise OutputValidationError(f"{issue_prefix}.date_unavailable")
        if item.effective_date is not None:
            if item.effective_date > bundle.analysis_date:
                raise OutputValidationError(f"{issue_prefix}.future_date")
            dates.append(item.effective_date)
            continue
        live_date = _live_snapshot_date(item, bundle=bundle)
        if live_date is None:
            raise OutputValidationError(f"{issue_prefix}.date_unavailable")
        if live_date > bundle.sealed_at.astimezone(market_timezone(bundle.instrument)).date():
            raise OutputValidationError(f"{issue_prefix}.future_date")
        dates.append(live_date)
        has_live_snapshot = True
    if not dates:
        raise OutputValidationError(f"{issue_prefix}.date_unavailable")
    return _ResolvedEvidenceDate(
        value=max(dates),
        temporal_basis=(
            NumericTemporalBasis.LIVE_SNAPSHOT
            if has_live_snapshot
            else NumericTemporalBasis.POINT_IN_TIME
        ),
    )


def _live_snapshot_date(item: EvidenceItem, *, bundle: EvidenceBundle) -> date | None:
    if not item.origins or any(
        origin.temporal_scope is not EvidenceTemporalScope.LIVE_ONLY or not origin.retrieved_at
        for origin in item.origins
    ):
        return None
    retrieved: list[datetime] = []
    for origin in item.origins:
        try:
            value = datetime.fromisoformat(str(origin.retrieved_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if value.utcoffset() is None or value > bundle.sealed_at:
            return None
        if not is_near_live(
            bundle.analysis_date.isoformat(),
            bundle.instrument,
            now=value,
        ):
            return None
        retrieved.append(value)
    timezone = market_timezone(bundle.instrument)
    return max(value.astimezone(timezone).date() for value in retrieved)
