"""Durable numeric-audit contracts used by research decisions."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ._contract_base import (
    _DECISION_COMPONENT_PATH_PATTERN,
    ArtifactGenerationMethod,
    FrozenModel,
    NumericAuditAppendixStatus,
    NumericAuditComponentType,
    NumericAuditPhase,
    NumericCalculationStatus,
    NumericDisplayScale,
    NumericDisplayStatus,
    ResearchScenarioKind,
    _unique_evidence_refs,
)


class NumericAuditSnapshot(FrozenModel):
    """One sanitized failed numeric serializer candidate."""

    phase: NumericAuditPhase
    method: ArtifactGenerationMethod
    reason_code: str = Field(pattern=r"^[a-z0-9_.-]+$")
    validation_issues: tuple[str, ...] = ()
    schema_valid: bool
    candidate: dict[str, Any] | None = None
    candidate_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    candidate_omitted: Literal["oversize"] | None = None

    @field_validator("validation_issues")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        issues = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"[a-z0-9_.-]+", item) for item in issues):
            raise ValueError("numeric audit issues must be stable codes")
        return issues


class NumericAuditOmission(FrozenModel):
    component_path: str = Field(pattern=r"^[a-z0-9_.-]+$")
    component_type: NumericAuditComponentType
    scenario_kind: ResearchScenarioKind | None = None
    reference_label: str | None = Field(default=None, min_length=1, max_length=200)
    issue_codes: tuple[str, ...] = Field(min_length=1)

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        issues = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"[a-z0-9_.-]+", item) for item in issues):
            raise ValueError("numeric audit issues must be stable codes")
        return issues


class NumericRequirementCheck(FrozenModel):
    """Auditable comparison between a stated value and a canonical result."""

    requirement_id: str = Field(pattern=r"^req_[a-z0-9][a-z0-9_.-]*$")
    calculation_id: str | None = Field(
        default=None,
        pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$",
    )
    component_path: str = Field(pattern=_DECISION_COMPONENT_PATH_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=200)
    stated_value: int | float
    fraction_digits: int = Field(ge=0, le=8)
    unit: str = Field(min_length=1, max_length=32)
    display_scale: NumericDisplayScale = NumericDisplayScale.BASE
    formula: str = Field(min_length=1)
    inputs: dict[str, int | float] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = ()
    canonical_result: int | float | None = None
    comparison_result: int | float | None = None
    comparison_difference: int | float | None = None
    rounded_stated_value: int | float | None = None
    rounded_canonical_result: int | float | None = None
    calculation_status: NumericCalculationStatus
    display_status: NumericDisplayStatus
    issue_codes: tuple[str, ...] = ()

    @field_validator("inputs")
    @classmethod
    def validate_inputs(
        cls,
        value: dict[str, int | float],
    ) -> dict[str, int | float]:
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) for key in value):
            raise ValueError("calculation input names must be identifiers")
        if any(isinstance(item, bool) for item in value.values()):
            raise ValueError("calculation inputs must be numeric")
        return value

    @field_validator("input_evidence_refs")
    @classmethod
    def validate_input_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("date_evidence_refs")
    @classmethod
    def validate_date_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_date_ref_subset(self) -> NumericRequirementCheck:
        if not set(self.date_evidence_refs).issubset(self.input_evidence_refs):
            raise ValueError("calculation date refs must belong to input evidence refs")
        return self

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        issues = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"[a-z0-9_.-]+", item) for item in issues):
            raise ValueError("numeric audit issues must be stable codes")
        return issues

    @model_validator(mode="after")
    def validate_status_fields(self) -> NumericRequirementCheck:
        if self.calculation_status is NumericCalculationStatus.VERIFIED:
            if self.calculation_id is None or self.canonical_result is None:
                raise ValueError("verified calculations require an ID and result")
            comparison_fields = (
                self.comparison_result,
                self.comparison_difference,
            )
            if any(item is not None for item in comparison_fields) and any(
                item is None for item in comparison_fields
            ):
                raise ValueError("display comparison fields must be all present or all absent")
            if self.display_status is NumericDisplayStatus.NOT_CHECKED:
                raise ValueError("verified calculations require a display comparison")
            if self.rounded_stated_value is None or self.rounded_canonical_result is None:
                raise ValueError("checked displays require both rounded values")
        elif self.display_status is not NumericDisplayStatus.NOT_CHECKED:
            raise ValueError("invalid or missing calculations cannot compare display")
        return self


class DecisionNumericAuditAppendix(FrozenModel):
    """Decision calculation comparisons and unverified numeric proposals."""

    status: NumericAuditAppendixStatus
    requirement_checks: tuple[NumericRequirementCheck, ...] = ()
    snapshots: tuple[NumericAuditSnapshot, ...] = Field(max_length=2)
    omitted_components: tuple[NumericAuditOmission, ...] = ()
