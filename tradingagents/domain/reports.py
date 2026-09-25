"""Reports contracts."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from tradingagents.domain.common import (
    DebateImportance,
    FrozenModel,
    ResearchRating,
    _StableStrEnum,
    _unique_evidence_refs,
    _unique_research_ids,
)


class AnalystClaimType(_StableStrEnum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    FORECAST = "forecast"


class ClaimImportance(_StableStrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"


class ReportAuditStatus(_StableStrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class ReportSection(FrozenModel):
    """A deterministic heading extracted from the human-readable report."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    title: str = Field(min_length=1)
    anchor: str = Field(min_length=1)
    source_refs: tuple[str, ...] = ()

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class KeyClaim(FrozenModel):
    """A decision-relevant assertion extracted from a readable report."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    kind: AnalystClaimType
    importance: ClaimImportance
    statement: str = Field(min_length=1)
    implication: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"ev_[a-f0-9]{12}", ref) for ref in refs):
            raise ValueError("key claims must use valid evidence refs")
        return refs


class ResearchWarning(FrozenModel):
    """Structured, plain-text warning suitable for APIs and audit exports."""

    code: str = Field(default="legacy.warning", pattern=r"^[a-z0-9_.-]+$")
    message: str = Field(min_length=1, max_length=2000)
    evidence_ref: str | None = Field(
        default=None,
        pattern=r"^ev_[a-f0-9]{12}$",
    )
    source: str | None = Field(default=None, max_length=200)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: Any) -> str:
        text = str(value)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"(\*\*|__|`)", "", text)
        return " ".join(text.split()).strip()


def _coerce_warnings(value: Any) -> tuple[ResearchWarning, ...]:
    if value is None:
        return ()
    items = (value,) if isinstance(value, (str, dict, ResearchWarning)) else value
    warnings = []
    for item in items:
        if isinstance(item, ResearchWarning):
            warning = item
        elif isinstance(item, str):
            warning = ResearchWarning(message=item)
        else:
            warning = ResearchWarning.model_validate(item)
        warnings.append(warning)
    return tuple(dict.fromkeys(warnings))


class AnalystReport(FrozenModel):
    """Readable analyst report with a deliberately small audit envelope."""

    analyst: Literal["market", "social", "news", "fundamentals"]
    markdown: str = Field(min_length=1)
    report_sections: tuple[ReportSection, ...] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    key_claims: tuple[KeyClaim, ...] = ()
    source_refs: tuple[str, ...] = ()
    audit_status: ReportAuditStatus
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_structure(self) -> AnalystReport:
        claim_ids = tuple(claim.id for claim in self.key_claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("analyst claim IDs must be unique")
        section_ids = tuple(section.id for section in self.report_sections)
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("analyst section IDs must be unique")
        if any(claim.section_id not in set(section_ids) for claim in self.key_claims):
            raise ValueError("key claims must identify an existing report section")
        used_refs = {ref for claim in self.key_claims for ref in claim.evidence_refs}
        used_refs.update(ref for section in self.report_sections for ref in section.source_refs)
        if not used_refs.issubset(self.source_refs):
            raise ValueError("report source refs must include claim and section refs")
        if self.audit_status is ReportAuditStatus.COMPLETE:
            if not any(claim.importance is ClaimImportance.PRIMARY for claim in self.key_claims):
                raise ValueError("complete report audit requires a primary claim")
            if any(not claim.evidence_refs for claim in self.key_claims):
                raise ValueError("complete report audit requires cited claims")
        return self


class DecisionBrief(FrozenModel):
    """Readable Final reasoning persisted before strict decision serialization."""

    markdown: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)


class ResearchCase(FrozenModel):
    """A readable constructive or skeptical research case."""

    role: Literal["bull", "bear"]
    markdown: str = Field(min_length=1)


class DebateIssue(FrozenModel):
    """One material question used only for graph routing and navigation."""

    id: str = Field(pattern=r"^debate\.issue_[a-z0-9][a-z0-9_.-]*$")
    question: str = Field(min_length=1)
    importance: DebateImportance


class DebateAgenda(FrozenModel):
    """Prioritized shallow agenda derived from the two readable cases."""

    summary: str = Field(min_length=1)
    issues: tuple[DebateIssue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_agenda(self) -> DebateAgenda:
        issue_ids = tuple(issue.id for issue in self.issues)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("debate issue IDs must be unique")
        return self


class RebuttalReview(FrozenModel):
    """One readable response plus the issue IDs needed by graph control."""

    role: Literal["bull", "bear"]
    round: int = Field(ge=1)
    markdown: str = Field(min_length=1)
    addressed_issue_ids: tuple[str, ...] = Field(min_length=1)
    open_issue_ids: tuple[str, ...] = ()

    @field_validator("addressed_issue_ids", "open_issue_ids")
    @classmethod
    def validate_issue_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_research_ids(value)


class IssueDisposition(FrozenModel):
    """A judge routing result without duplicating the readable rationale."""

    issue_id: str = Field(pattern=r"^debate\.issue_[a-z0-9][a-z0-9_.-]*$")
    status: Literal["upheld", "rejected", "unresolved"]


class JudgeDraft(FrozenModel):
    """Readable preliminary judgment with shallow issue dispositions."""

    markdown: str = Field(min_length=1)
    preliminary_rating: ResearchRating | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    issue_dispositions: tuple[IssueDisposition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_draft(self) -> JudgeDraft:
        issue_ids = tuple(item.issue_id for item in self.issue_dispositions)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("judge dispositions must use unique issue IDs")
        return self


class RiskReview(FrozenModel):
    """A readable challenge with only navigation metadata typed."""

    role: Literal["integrated", "aggressive", "neutral", "conservative"]
    markdown: str = Field(min_length=1)
    challenged_issue_ids: tuple[str, ...] = ()
    unresolved_issue_ids: tuple[str, ...] = ()

    @field_validator("challenged_issue_ids", "unresolved_issue_ids")
    @classmethod
    def validate_issue_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_research_ids(value)
