"""Bounded numeric generation, repair and redacted audit snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any

from tradingagents.domain.common import (
    NumericAuditAppendixStatus,
    NumericAuditComponentType,
    NumericAuditPhase,
    NumericAuditStatus,
    ScenarioReferenceCategory,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
)
from tradingagents.domain.numeric_audit import (
    DecisionNumericAuditAppendix,
    NumericAuditOmission,
    NumericAuditSnapshot,
    NumericRequirementCheck,
)
from tradingagents.domain.reports import (
    ResearchWarning,
)
from tradingagents.research.synthesis.decision_prompts import (
    _decision_example_text,
    _decision_language_rules,
    _numeric_example_pair,
    decision_display_scale_guidance,
    decision_percentage_calculation_guidance,
    decision_reference_label_guidance,
)
from tradingagents.research.synthesis.drafts import (
    CalculationInputDraft,
    CalculationRecordDraft,
    DecisionNumericDraft,
    DecisionNumericRequirementDraft,
    DerivedMarketReferenceLevelDraft,
    DerivedRangeEndpointDraft,
    EventWriter,
    MarketReferenceLevelDraft,
    ObservedMarketReferenceLevelDraft,
    ObservedRangeEndpointDraft,
    ResearchScenarioCoreDraft,
    ScenarioReferenceRangeDraft,
    ScenarioReferenceRangesDraft,
    ValuationAssessmentDraft,
)
from tradingagents.research.synthesis.generation import _configured_generation_method
from tradingagents.research.synthesis.numeric_audit import (
    _assemble_numeric_draft,
    _empty_numeric_assembly,
    _missing_requirement_checks,
    _NumericDecisionAssembly,
    _requirement_omissions,
)
from tradingagents.research.synthesis.numeric_evidence import (
    build_numeric_value_catalog,
    compact_numeric_value_catalog,
)
from tradingagents.research.synthesis.output_validation import (
    OutputValidationError,
)
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputError,
    StructuredOutputFailure,
    StructuredOutputRunner,
)


def _emit_numeric_normalization_event(
    assembly: _NumericDecisionAssembly,
    *,
    event_writer: EventWriter | None,
    node: str,
) -> _NumericDecisionAssembly:
    if event_writer is not None and assembly.promoted_singletons:
        event_writer(
            {
                "event_type": "decision.numeric_singleton_promoted",
                "node": node,
                "payload": {"count": assembly.promoted_singletons},
            }
        )
    if event_writer is not None and assembly.reordered_ranges:
        event_writer(
            {
                "event_type": "decision.numeric_range_reordered",
                "node": node,
                "payload": {"count": assembly.reordered_ranges},
            }
        )
    if event_writer is not None and assembly.audit_issues:
        event_writer(
            {
                "event_type": "node.numeric_audit_degraded",
                "node": node,
                "payload": {
                    "reason_code": "numeric_display_mismatch",
                    "validation_issues": list(assembly.audit_issues),
                },
            }
        )
    return assembly


def _numeric_assembly_requires_repair(
    assembly: _NumericDecisionAssembly,
) -> bool:
    """Return whether another numeric serializer call can change the result."""

    return bool(assembly.repair_issues)


def _invoke_decision_numeric(
    llm: Any,
    *,
    prompt: str,
    node: str,
    bundle: EvidenceBundle,
    allowed_evidence_refs: tuple[str, ...],
    event_writer: EventWriter | None,
    output_language: str,
    core_scenarios: tuple[ResearchScenarioCoreDraft, ...],
    requirements: tuple[DecisionNumericRequirementDraft, ...],
) -> _NumericDecisionAssembly:
    allowed = set(allowed_evidence_refs)
    value_catalog = build_numeric_value_catalog(
        bundle,
        allowed_evidence_refs=allowed,
    )
    value_catalog_by_id = {item.id: item for item in value_catalog}
    value_catalog_prompt = compact_numeric_value_catalog(value_catalog)
    example_text = _decision_example_text(output_language)
    language_rules = _decision_language_rules(output_language)
    percentage_rules = decision_percentage_calculation_guidance()
    display_scale_rules = decision_display_scale_guidance()
    reference_label_rules = decision_reference_label_guidance(output_language)
    scenario_catalog = tuple(
        {
            "kind": scenario.kind.value,
            "outcome": scenario.outcome,
            "core_assumptions": list(scenario.core_assumptions),
            "evidence_refs": list(scenario.evidence_refs),
        }
        for scenario in core_scenarios
    )
    scenario_catalog_json = json.dumps(scenario_catalog, ensure_ascii=False)
    requirement_catalog_json = json.dumps(
        [item.model_dump(mode="json") for item in requirements],
        ensure_ascii=False,
    )

    def validate(draft: DecisionNumericDraft) -> DecisionNumericDraft:
        assembly = _assemble_numeric_draft(
            draft,
            bundle=bundle,
            allowed_evidence_refs=allowed,
            value_catalog=value_catalog_by_id,
            salvage=True,
            node=node,
            output_language=output_language,
            requirements=requirements,
        )
        if _numeric_assembly_requires_repair(assembly):
            raise OutputValidationError(
                assembly.repair_issues[0],
                issue_codes=assembly.repair_issues,
            )
        return draft

    def numeric_event(raw: dict[str, Any]) -> None:
        if event_writer is None:
            return
        mapped = {
            "node.output_retry": "node.numeric_audit_retry",
            "node.output_recovered": "node.numeric_audit_recovered",
            "node.output_failed": "node.numeric_audit_degraded",
        }.get(raw.get("event_type"), raw.get("event_type"))
        event_writer({**raw, "event_type": mapped})

    example_reference: MarketReferenceLevelDraft
    if value_catalog:
        example_reference = ObservedMarketReferenceLevelDraft(
            label=example_text["reference_label"],
            value_ref=value_catalog[0].id,
            interpretation=example_text["reference_interpretation"],
        )
    else:
        example_reference = DerivedMarketReferenceLevelDraft(
            label=example_text["reference_label"],
            interpretation=example_text["reference_interpretation"],
            calculation_id="calc_valuation_low",
        )

    example_ranges: list[ScenarioReferenceRangeDraft] = []
    example_pair = _numeric_example_pair(value_catalog)
    if example_pair is not None:
        example_low, example_high = example_pair
        example_ranges.append(
            ScenarioReferenceRangeDraft(
                category=ScenarioReferenceCategory.TECHNICAL,
                label=example_text["scenario_range_label"],
                low=ObservedRangeEndpointDraft(value_ref=example_low.id),
                high=ObservedRangeEndpointDraft(value_ref=example_high.id),
                interpretation=example_text["scenario_range_interpretation"],
                limitations=(example_text["valuation_limitation"],),
            )
        )

    example_calculations = [
        CalculationRecordDraft(
            id="calc_valuation_low",
            formula="earnings * multiple",
            inputs=(
                CalculationInputDraft(
                    name="earnings",
                    value=10,
                    date_evidence_refs=(allowed_evidence_refs[0],),
                ),
                CalculationInputDraft(name="multiple", value=10),
            ),
            input_evidence_refs=(allowed_evidence_refs[0],),
            unit="USD",
            limitations=(example_text["valuation_limitation"],),
        ),
        CalculationRecordDraft(
            id="calc_valuation_high",
            formula="earnings * multiple",
            inputs=(
                CalculationInputDraft(
                    name="earnings",
                    value=11,
                    date_evidence_refs=(allowed_evidence_refs[0],),
                ),
                CalculationInputDraft(name="multiple", value=10),
            ),
            input_evidence_refs=(allowed_evidence_refs[0],),
            unit="USD",
            limitations=(example_text["valuation_limitation"],),
        ),
    ]
    if requirements:
        requirement = requirements[0]
        example_calculations.append(
            CalculationRecordDraft(
                id="calc_decision_requirement",
                formula=requirement.formula,
                inputs=requirement.inputs,
                input_evidence_refs=requirement.input_evidence_refs,
                unit=requirement.unit,
                limitations=requirement.limitations,
                requirement_ids=(requirement.id,),
            )
        )
    example = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=tuple(example_ranges),
        ),
        valuation_assessment=ValuationAssessmentDraft(
            method=example_text["valuation_method"],
            low=DerivedRangeEndpointDraft(
                calculation_id="calc_valuation_low",
            ),
            high=DerivedRangeEndpointDraft(
                calculation_id="calc_valuation_high",
            ),
            limitations=(example_text["valuation_limitation"],),
        ),
        market_reference_levels=(example_reference,),
        calculation_records=tuple(example_calculations),
    )
    runner = StructuredOutputRunner(
        llm=llm,
        schema=DecisionNumericDraft,
        validator=validate,
        node=node,
        event_writer=numeric_event,
        repair_mode="preferred",
        include_candidate_in_repair=True,
        candidate_only_repair=True,
        invoke_config={"metadata": {"research_node": node}},
        repair_instructions=(
            "Repair only the optional numeric appendix. Calculation input "
            "names must be ASCII identifiers and the formula must use every "
            "input exactly. Technical levels, historical highs/lows, and analyst "
            "target prices are observed only when selected by value_ref from the "
            "Numeric Value Catalog. Rounded, selected, combined, or model-interpreted "
            "levels must use basis=interpreted with anchor_value_refs from the Numeric "
            "Value Catalog; context_evidence_refs are explanatory only and never set "
            "the value date. Observed and interpreted measurements are inherited from "
            "their catalog entries; derived measurements come from the calculation "
            "unit. Do not supply or override units on ranges or market references. "
            "Interpreted values require no calculation, but EPS times "
            "a multiple, DCF, and other arithmetic must use basis=derived with a valid "
            "calculation rather than being disguised as interpreted values. "
            "Each base, bull, and bear scenario range field is an array. Preserve "
            "every already-valid, non-duplicate range while repairing only the "
            "invalid range identified by the issue path. A scenario may contain "
            "multiple ranges with the same category when their labels or endpoints "
            "describe distinct research uses. A true range must contain two distinct "
            "endpoints with low strictly less than high; never reverse low and high. "
            "Represent a single numeric level in market_reference_levels, never as a "
            "zero-width range. Do not emit exact duplicates. "
            "Every range must belong to the matching validated scenario in the "
            "SCENARIO CATALOG. Labels describe only the range purpose and must not "
            "claim to belong to a different base, bull, or bear scenario. Labels must "
            "not repeat dates, values, units, basis names, or scenario ownership; the "
            "application renders those fields separately. "
            "A valuation assessment is allowed only when both endpoints are derived "
            "from real valuation calculations such as EPS times a multiple or DCF. Do not "
            "supply calculation results or dates; the application derives both "
            "from the formula and Evidence Ledger. Do not change the qualitative "
            "decision core. Every item in DECISION NUMERIC REQUIREMENTS must be "
            "covered by a calculation whose requirement_ids includes that item's ID. "
            "Copy its formula, named inputs, Evidence refs, unit, and limitations "
            "without changing them, including every input's date_evidence_refs. "
            "Those refs date the calculation; explanatory Evidence must not be added. "
            "When requirements are present, requested must be "
            f"true. {percentage_rules} {display_scale_rules} "
            f"{reference_label_rules} {language_rules}\n"
            "VALID OBSERVED VALUE REFS:\n"
            + json.dumps(value_catalog_prompt, ensure_ascii=False)
            + "\nSCENARIO CATALOG:\n"
            + scenario_catalog_json
            + "\nDECISION NUMERIC REQUIREMENTS:\n"
            + requirement_catalog_json
        ),
    )
    try:
        output = runner.invoke(
            prompt + "\n\nExtract only optional decision-critical numeric content. "
            "Set requested=false and return empty collections only when the brief "
            "does not support a numeric appendix and DECISION NUMERIC REQUIREMENTS "
            "is empty. Do not copy ordinary report "
            "table arithmetic. Use scenario_reference_ranges for technical bands, "
            "52-week levels, or analyst target ranges; these are not valuations. "
            "A true range requires two distinct endpoints with low strictly less than "
            "high. Put a single numeric level in market_reference_levels instead of "
            "repeating it as low and high. Labels name only the metric or research use "
            "and must omit dates, values, units, basis names, and scenario ownership. "
            "Do not supply units on ranges, valuation assessments, or market references; "
            "the application inherits them from catalog anchors or calculations. "
            "Use valuation_assessment only for genuinely derived valuation work. "
            + percentage_rules
            + " "
            + display_scale_rules
            + " "
            + reference_label_rules
            + " "
            + language_rules
            + "\n\nNUMERIC VALUE CATALOG:\n"
            + json.dumps(value_catalog_prompt, ensure_ascii=False)
            + "\n\nSCENARIO CATALOG:\n"
            + scenario_catalog_json
            + "\n\nDECISION NUMERIC REQUIREMENTS:\n"
            + requirement_catalog_json
            + "\n\nLOCALIZED VALID EXAMPLE:\n"
            + json.dumps(example.model_dump(mode="json"), ensure_ascii=False),
            example=example.model_dump(mode="json"),
            allowed_evidence_refs=allowed_evidence_refs,
        )
    except StructuredOutputError as exc:
        attempted_method = (
            exc.failures[-1].method if exc.failures else _configured_generation_method(llm)
        )
        draft = _numeric_candidate(exc.candidate)
        if draft is None:
            omissions = _requirement_omissions(
                requirements,
                issue_suffix="missing_calculation",
            )
            empty = _empty_numeric_assembly(
                node=node,
                status=(
                    NumericAuditStatus.PARTIAL if requirements else NumericAuditStatus.INCOMPLETE
                ),
                requirement_checks=_missing_requirement_checks(
                    requirements,
                    issue_suffix="missing_calculation",
                ),
            )
            return _emit_numeric_normalization_event(
                replace(
                    empty,
                    generation_method=attempted_method,
                    audit=_numeric_audit_appendix(
                        status=NumericAuditAppendixStatus.INCOMPLETE,
                        failures=exc.failures,
                        omissions=omissions
                        or (
                            NumericAuditOmission(
                                component_path="numeric.appendix",
                                component_type=NumericAuditComponentType.APPENDIX,
                                issue_codes=tuple(
                                    dict.fromkeys(
                                        issue
                                        for failure in exc.failures
                                        for issue in failure.validation_issues
                                    )
                                )
                                or ("numeric.appendix.invalid",),
                            ),
                        ),
                        requirement_checks=empty.requirement_checks,
                    ),
                ),
                event_writer=event_writer,
                node=node,
            )
        assembly = _assemble_numeric_draft(
            draft,
            bundle=bundle,
            allowed_evidence_refs=allowed,
            value_catalog=value_catalog_by_id,
            salvage=True,
            node=node,
            requirements=requirements,
        )
        if _numeric_repair_is_noop(exc.failures):
            assembly = replace(
                assembly,
                warnings=(
                    *assembly.warnings,
                    ResearchWarning(
                        code="decision.numeric_repair_noop",
                        message=(
                            "The numeric repair returned the same invalid appendix; "
                            "independently valid components were retained."
                        ),
                        source=node,
                    ),
                ),
            )
        return _emit_numeric_normalization_event(
            replace(
                assembly,
                generation_method=attempted_method,
                audit=_numeric_audit_appendix(
                    status=(
                        NumericAuditAppendixStatus.PARTIAL
                        if assembly.status is NumericAuditStatus.PARTIAL
                        else NumericAuditAppendixStatus.INCOMPLETE
                    ),
                    failures=exc.failures,
                    omissions=assembly.omissions,
                    requirement_checks=assembly.requirement_checks,
                ),
            ),
            event_writer=event_writer,
            node=node,
        )
    assembly = _assemble_numeric_draft(
        output.value,
        bundle=bundle,
        allowed_evidence_refs=allowed,
        value_catalog=value_catalog_by_id,
        salvage=False,
        node=node,
        requirements=requirements,
    )
    assembly = replace(
        assembly,
        generation_method=output.generation_method,
    )
    if output.failed_attempts:
        return _emit_numeric_normalization_event(
            replace(
                assembly,
                audit=_numeric_audit_appendix(
                    status=NumericAuditAppendixStatus.RECOVERED,
                    failures=output.failed_attempts,
                    omissions=(),
                    requirement_checks=assembly.requirement_checks,
                ),
            ),
            event_writer=event_writer,
            node=node,
        )
    return _emit_numeric_normalization_event(
        assembly,
        event_writer=event_writer,
        node=node,
    )


def _numeric_candidate(candidate: dict[str, Any] | None) -> DecisionNumericDraft | None:
    if candidate is None:
        return None
    try:
        return DecisionNumericDraft.model_validate(candidate)
    except Exception:
        return None


_NUMERIC_CANDIDATE_MAX_BYTES = 256 * 1024


_SENSITIVE_CANDIDATE_KEY = re.compile(r"(?i)(api.?key|authorization|bearer|password|secret|token)")


_SENSITIVE_CANDIDATE_VALUE = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|token)"
    r"(\s*[:=]\s*)(\S+)"
)


def _numeric_audit_appendix(
    *,
    status: NumericAuditAppendixStatus,
    failures: tuple[StructuredOutputFailure, ...],
    omissions: tuple[NumericAuditOmission, ...],
    requirement_checks: tuple[NumericRequirementCheck, ...] = (),
) -> DecisionNumericAuditAppendix:
    snapshots = tuple(_numeric_audit_snapshot(failure) for failure in failures)
    return DecisionNumericAuditAppendix(
        status=status,
        requirement_checks=requirement_checks,
        snapshots=snapshots[-2:],
        omitted_components=omissions,
    )


def _numeric_audit_snapshot(
    failure: StructuredOutputFailure,
) -> NumericAuditSnapshot:
    candidate = _sanitize_numeric_candidate(failure.candidate)
    if candidate is None:
        return NumericAuditSnapshot(
            phase=NumericAuditPhase(failure.phase),
            method=failure.method,
            reason_code=failure.reason_code,
            validation_issues=failure.validation_issues,
            schema_valid=False,
        )
    encoded = json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    schema_valid = _numeric_candidate(candidate) is not None
    if len(encoded) > _NUMERIC_CANDIDATE_MAX_BYTES:
        return NumericAuditSnapshot(
            phase=NumericAuditPhase(failure.phase),
            method=failure.method,
            reason_code=failure.reason_code,
            validation_issues=failure.validation_issues,
            schema_valid=schema_valid,
            candidate_digest=digest,
            candidate_omitted="oversize",
        )
    return NumericAuditSnapshot(
        phase=NumericAuditPhase(failure.phase),
        method=failure.method,
        reason_code=failure.reason_code,
        validation_issues=failure.validation_issues,
        schema_valid=schema_valid,
        candidate=candidate,
        candidate_digest=digest,
    )


def _numeric_repair_is_noop(
    failures: tuple[StructuredOutputFailure, ...],
) -> bool:
    if len(failures) < 2:
        return False
    initial, repair = failures[-2:]
    initial_snapshot = _numeric_audit_snapshot(initial)
    repair_snapshot = _numeric_audit_snapshot(repair)
    return bool(
        initial_snapshot.candidate_digest
        and initial_snapshot.candidate_digest == repair_snapshot.candidate_digest
        and initial_snapshot.validation_issues == repair_snapshot.validation_issues
    )


def _sanitize_numeric_candidate(
    candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if candidate is None:
        return None

    def sanitize(value: Any, key: str | None = None) -> Any:
        if key is not None and _SENSITIVE_CANDIDATE_KEY.search(key):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(item_key): sanitize(item_value, str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        if isinstance(value, str):
            return _SENSITIVE_CANDIDATE_VALUE.sub(
                r"\1\2[REDACTED]",
                value,
            )
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return str(value)

    sanitized = sanitize(candidate)
    return sanitized if isinstance(sanitized, dict) else None
