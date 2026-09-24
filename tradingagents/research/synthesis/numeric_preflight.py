"""Normalize numeric requirements before model serialization."""

from __future__ import annotations

import ast
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import (
    BaseModel,
    ValidationError,
)

from tradingagents.domain.common import (
    NumericAuditComponentType,
    NumericDisplayScale,
)
from tradingagents.domain.numeric_audit import (
    NumericAuditOmission,
)
from tradingagents.research.synthesis.decision_prompts import _decision_component_text
from tradingagents.research.synthesis.diagnostics import requirement_diagnostic
from tradingagents.research.synthesis.drafts import (
    DecisionNumericRequirementDraft,
    EventWriter,
    ResearchDecisionCoreEnvelope,
)
from tradingagents.research.synthesis.numeric_math import (
    _calculation_date_refs,
    _requires_base_display_scale,
)
from tradingagents.research.synthesis.output_validation import (
    OutputValidationError,
    require_valid_refs,
)


@dataclass(frozen=True)
class _NumericRequirementPreflight:
    requirements: tuple[DecisionNumericRequirementDraft, ...]
    issues: tuple[str, ...]
    omissions: tuple[NumericAuditOmission, ...]
    normalized_display_scales: int = 0


_NUMERIC_REQUIREMENT_ERROR_REASONS = {
    "dict_type": "object_type",
    "extra_forbidden": "extra.forbidden",
    "finite_number": "non_finite",
    "float_parsing": "number_type",
    "float_type": "number_type",
    "greater_than_equal": "range",
    "int_parsing": "integer_type",
    "int_type": "integer_type",
    "less_than_equal": "range",
    "list_type": "list_type",
    "literal_error": "enum",
    "missing": "missing",
    "model_type": "object_type",
    "string_pattern_mismatch": "pattern",
    "string_too_long": "too_long",
    "string_too_short": "too_short",
    "string_type": "string_type",
    "too_long": "too_long",
    "too_short": "too_short",
    "tuple_type": "list_type",
    "value_error": "invalid",
}


def _numeric_requirement_validation_issues(
    prefix: str,
    error: ValidationError,
) -> tuple[str, ...]:
    issues: list[str] = []
    for detail in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        error_type = str(detail.get("type") or "schema_invalid")
        reason = _NUMERIC_REQUIREMENT_ERROR_REASONS.get(
            error_type,
            re.sub(r"[^a-z0-9_.-]+", "_", error_type.lower()),
        )
        raw_location = detail.get("loc") or ()
        location = [
            str(part)
            for part in raw_location
            if isinstance(part, int)
            or (isinstance(part, str) and re.fullmatch(r"[a-zA-Z0-9_-]+", part))
        ]
        if error_type == "extra_forbidden" and location:
            location.pop()
        segments = [prefix, *location, reason]
        issues.append(".".join(segments))
    return tuple(dict.fromkeys(issues)) or (f"{prefix}.schema_invalid",)


def _normalize_numeric_requirement_candidate(candidate: Any) -> Any:
    """Canonicalize unambiguous non-ASCII-safe operands before validation."""

    if isinstance(candidate, BaseModel):
        return candidate
    if not isinstance(candidate, Mapping):
        return candidate
    raw_inputs = candidate.get("inputs")
    formula = candidate.get("formula")
    if not isinstance(raw_inputs, (list, tuple)) or not isinstance(formula, str):
        return candidate
    names = [item.get("name") if isinstance(item, Mapping) else None for item in raw_inputs]
    if not names or any(not isinstance(name, str) for name in names):
        return candidate
    if all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in names):
        return candidate
    normalized_names = [unicodedata.normalize("NFKC", name) for name in names]
    normalized_formula = unicodedata.normalize("NFKC", formula)
    if len(set(normalized_names)) != len(normalized_names):
        return candidate
    digit_prefixed_identifier = re.compile(r"^(?=[0-9A-Za-z_]*[A-Za-z_])[0-9][0-9A-Za-z_]*$")
    if any(
        not name.isidentifier() and not digit_prefixed_identifier.fullmatch(name)
        for name in normalized_names
    ):
        return candidate
    replacements = {name: f"v{index}" for index, name in enumerate(normalized_names, start=1)}
    operand_pattern = re.compile(
        r"(?<!\w)(?:"
        + "|".join(re.escape(name) for name in sorted(normalized_names, key=len, reverse=True))
        + r")(?!\w)"
    )
    matched_names: set[str] = set()

    def replace_operand(match: re.Match[str]) -> str:
        name = match.group(0)
        matched_names.add(name)
        return replacements[name]

    rewritten_formula = operand_pattern.sub(replace_operand, normalized_formula)
    if matched_names != set(normalized_names):
        return candidate
    try:
        tree = ast.parse(rewritten_formula, mode="eval")
    except SyntaxError:
        return candidate
    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    if referenced_names != set(replacements.values()):
        return candidate
    normalized_inputs = [
        {**dict(item), "name": replacements[name]}
        for item, name in zip(raw_inputs, normalized_names, strict=True)
    ]
    return {
        **dict(candidate),
        "formula": ast.unparse(tree),
        "inputs": normalized_inputs,
    }


def _preflight_numeric_requirements(
    envelope: ResearchDecisionCoreEnvelope,
    *,
    valid_evidence_refs: set[str],
    event_writer: EventWriter | None = None,
    node: str = "committee.final.numeric",
) -> _NumericRequirementPreflight:
    requirements: list[DecisionNumericRequirementDraft] = []
    issues: list[str] = []
    omissions: list[NumericAuditOmission] = []
    seen_ids: set[str] = set()
    normalized_display_scale_ids: set[str] = set()
    core = envelope.qualitative_core()
    rejected_count = 0

    def record_rejection(index: int, original: Any, normalized: Any, codes: tuple[str, ...]) -> None:
        nonlocal rejected_count
        rejected_count += 1
        if event_writer is not None and rejected_count <= 8:
            payload = {"candidate_index": index, "validation_issues": list(codes),
                       "original": requirement_diagnostic(original),
                       "normalization_changed": original != normalized}
            if original != normalized:
                payload["normalized"] = requirement_diagnostic(normalized)
            event_writer({"event_type": "decision.numeric_candidate_rejected",
                          "node": node, "payload": payload})

    for index, candidate in enumerate(envelope.numeric_requirement_candidates):
        original_candidate = candidate
        candidate = _normalize_numeric_requirement_candidate(candidate)
        prefix = f"numeric.requirement_candidate.{index}"
        candidate_path = prefix
        candidate_label: str | None = None
        if isinstance(candidate, Mapping):
            raw_path = candidate.get("component_path")
            if isinstance(raw_path, str) and re.fullmatch(r"[a-z0-9_.-]+", raw_path):
                candidate_path = raw_path
            raw_label = candidate.get("label")
            if isinstance(raw_label, str) and 0 < len(raw_label) <= 200:
                candidate_label = raw_label
        try:
            requirement = DecisionNumericRequirementDraft.model_validate(candidate)
        except ValidationError as exc:
            candidate_issues = _numeric_requirement_validation_issues(prefix, exc)
            issues.extend(candidate_issues)
            omissions.append(
                NumericAuditOmission(
                    component_path=candidate_path,
                    component_type=NumericAuditComponentType.DECISION_CLAIM,
                    reference_label=candidate_label,
                    issue_codes=candidate_issues,
                )
            )
            record_rejection(index, original_candidate, candidate, omissions[-1].issue_codes)
            continue
        except (TypeError, ValueError):
            issue = f"{prefix}.schema_invalid"
            issues.append(issue)
            omissions.append(
                NumericAuditOmission(
                    component_path=candidate_path,
                    component_type=NumericAuditComponentType.DECISION_CLAIM,
                    reference_label=candidate_label,
                    issue_codes=(issue,),
                )
            )
            record_rejection(index, original_candidate, candidate, omissions[-1].issue_codes)
            continue
        normalized_display_scale = False
        if (
            _requires_base_display_scale(requirement.unit)
            and requirement.display_scale is not NumericDisplayScale.BASE
        ):
            requirement = requirement.model_copy(update={"display_scale": NumericDisplayScale.BASE})
            normalized_display_scale = True
        if requirement.id in seen_ids:
            issue = f"{prefix}.duplicate_id"
            issues.append(issue)
            omissions.append(
                NumericAuditOmission(
                    component_path=requirement.component_path,
                    component_type=NumericAuditComponentType.DECISION_CLAIM,
                    reference_label=requirement.label,
                    issue_codes=(issue,),
                )
            )
            record_rejection(index, original_candidate, candidate, omissions[-1].issue_codes)
            continue
        if _decision_component_text(core, requirement.component_path) is None:
            issue = f"{prefix}.unknown_component"
            issues.append(issue)
            omissions.append(
                NumericAuditOmission(
                    component_path=requirement.component_path,
                    component_type=NumericAuditComponentType.DECISION_CLAIM,
                    reference_label=requirement.label,
                    issue_codes=(issue,),
                )
            )
            record_rejection(index, original_candidate, candidate, omissions[-1].issue_codes)
            continue
        try:
            require_valid_refs(
                requirement.input_evidence_refs,
                valid_evidence_refs,
                required=True,
            )
        except OutputValidationError:
            issue = f"{prefix}.invalid_evidence"
            issues.append(issue)
            omissions.append(
                NumericAuditOmission(
                    component_path=requirement.component_path,
                    component_type=NumericAuditComponentType.DECISION_CLAIM,
                    reference_label=requirement.label,
                    issue_codes=(issue,),
                )
            )
            record_rejection(index, original_candidate, candidate, omissions[-1].issue_codes)
            continue
        date_evidence_refs = set(_calculation_date_refs(requirement.inputs))
        date_ref_issues: list[str] = []
        invalid_date_refs = date_evidence_refs - valid_evidence_refs
        if invalid_date_refs:
            date_ref_issues.append(f"{prefix}.date_refs.invalid_evidence")
        valid_date_refs = date_evidence_refs & valid_evidence_refs
        if not valid_date_refs.issubset(requirement.input_evidence_refs):
            date_ref_issues.append(f"{prefix}.date_refs.not_input_refs")
        if date_ref_issues:
            issues.extend(date_ref_issues)
            omissions.append(
                NumericAuditOmission(
                    component_path=requirement.component_path,
                    component_type=NumericAuditComponentType.DECISION_CLAIM,
                    reference_label=requirement.label,
                    issue_codes=tuple(date_ref_issues),
                )
            )
            record_rejection(index, original_candidate, candidate, omissions[-1].issue_codes)
            continue
        seen_ids.add(requirement.id)
        if normalized_display_scale:
            normalized_display_scale_ids.add(requirement.id)
        requirements.append(requirement)
    grouped = {
        group_id: tuple(item for item in requirements if item.display_group_id == group_id)
        for group_id in {
            item.display_group_id for item in requirements if item.display_group_id is not None
        }
    }
    invalid_group_requirement_ids: set[str] = set()
    for group_id, members in grouped.items():
        roles = {item.display_role for item in members}
        consistent = (
            len({(item.component_path, item.unit, item.display_scale) for item in members}) == 1
        )
        if len(members) == 2 and roles == {"range_low", "range_high"} and consistent:
            continue
        issue = f"numeric.requirement_group.{group_id}.invalid"
        issues.append(issue)
        for item in members:
            invalid_group_requirement_ids.add(item.id)
            omissions.append(
                NumericAuditOmission(
                    component_path=item.component_path,
                    component_type=NumericAuditComponentType.DECISION_CLAIM,
                    reference_label=item.label,
                    issue_codes=(issue,),
                )
            )
    if invalid_group_requirement_ids:
        requirements = [
            item for item in requirements if item.id not in invalid_group_requirement_ids
        ]
    if envelope.numeric_requirements_declared and not requirements:
        issue = "numeric.requirements.declared_missing"
        issues.append(issue)
        omissions.append(
            NumericAuditOmission(
                component_path="numeric.requirements",
                component_type=NumericAuditComponentType.DECISION_CLAIM,
                issue_codes=(issue,),
            )
        )
    if event_writer is not None and rejected_count > 8:
        event_writer({"event_type": "decision.numeric_candidate_diagnostics_limited",
                      "node": node, "payload": {"omitted_count": rejected_count - 8}})
    return _NumericRequirementPreflight(
        requirements=tuple(requirements),
        issues=tuple(issues),
        omissions=tuple(omissions),
        normalized_display_scales=sum(
            item.id in normalized_display_scale_ids for item in requirements
        ),
    )
