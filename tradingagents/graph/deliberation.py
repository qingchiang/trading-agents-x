"""Readable research deliberation with shallow routing contracts."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    AuditedRangeEndpoint,
    CalculationRecord,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    DecisionCalculationUse,
    DecisionNumericAuditAppendix,
    EvidenceBundle,
    EvidenceItem,
    EvidenceTemporalScope,
    EvidenceValueLocator,
    IssueDisposition,
    JudgeDraft,
    MarketReferenceBasis,
    MarketReferenceLevel,
    MeasurementKind,
    MemoryContext,
    NumericAuditAppendixStatus,
    NumericAuditComponentType,
    NumericAuditOmission,
    NumericAuditPhase,
    NumericAuditSnapshot,
    NumericAuditStatus,
    NumericCalculationStatus,
    NumericDisplayScale,
    NumericDisplayStatus,
    NumericRequirementCheck,
    NumericTemporalBasis,
    RebuttalReview,
    ReportLanguage,
    ResearchCase,
    ResearchDecision,
    ResearchRating,
    ResearchScenario,
    ResearchScenarioKind,
    ResearchWarning,
    RiskReview,
    RiskReviewAdjustment,
    RiskReviewDisposition,
    ScenarioReferenceCategory,
    ScenarioReferenceRange,
    ValuationAssessment,
)
from tradingagents.application.markdown_evidence import normalize_evidence_markdown
from tradingagents.dataflows.lookahead import is_near_live
from tradingagents.dataflows.symbol_utils import market_timezone
from tradingagents.graph.numeric_evidence import (
    NumericValueCatalogEntry,
    build_numeric_value_catalog,
    compact_numeric_value_catalog,
)
from tradingagents.graph.output_validation import (
    OutputValidationError,
    require_nonempty_texts,
    require_text,
    require_valid_refs,
)
from tradingagents.graph.structured_output import (
    StructuredOutputError,
    StructuredOutputFailure,
    StructuredOutputResult,
    StructuredOutputRunner,
)

EventWriter = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ResearchMarkdown:
    """One readable artifact body and non-fatal citation warnings."""

    markdown: str
    evidence_refs: tuple[str, ...]
    warnings: tuple[ResearchWarning, ...]


class RebuttalAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    addressed_issue_ids: tuple[str, ...] = Field(min_length=1)
    open_issue_ids: tuple[str, ...] = ()


class JudgeAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preliminary_rating: ResearchRating
    confidence: float = Field(ge=0.0, le=1.0)
    issue_dispositions: tuple[IssueDisposition, ...] = Field(min_length=1)


class CalculationInputDraft(BaseModel):
    """Serializer-facing numeric input with a schema-visible identifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    value: int | float
    date_evidence_refs: tuple[str, ...] = ()


class CalculationRecordDraft(BaseModel):
    """Serializer-facing calculation without dynamic JSON object keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")
    formula: str = Field(min_length=1)
    inputs: tuple[CalculationInputDraft, ...] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    unit: str = Field(min_length=1, max_length=32)
    limitations: tuple[str, ...] = Field(min_length=1)
    requirement_ids: tuple[str, ...] = ()

    @field_validator("inputs")
    @classmethod
    def validate_unique_inputs(
        cls,
        value: tuple[CalculationInputDraft, ...],
    ) -> tuple[CalculationInputDraft, ...]:
        names = tuple(item.name for item in value)
        if len(names) != len(set(names)):
            raise ValueError("calculation input names must be unique")
        return value

    def input_mapping(self) -> dict[str, int | float]:
        return {item.name: item.value for item in self.inputs}

    @field_validator("requirement_ids")
    @classmethod
    def validate_requirement_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        result = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"req_[a-z0-9][a-z0-9_.-]*", item) for item in result):
            raise ValueError("invalid numeric requirement identifier")
        return result


class DecisionNumericRequirementDraft(BaseModel):
    """A derived number used by one strict qualitative decision component."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^req_[a-z0-9][a-z0-9_.-]*$")
    component_path: str = Field(
        pattern=(
            r"^(?:executive_summary|thesis|catalysts\.\d+|risks\.\d+|"
            r"invalidation_conditions\.\d+|"
            r"scenarios\.(?:base|bull|bear)\."
            r"(?:outcome|core_assumptions\.\d+)|"
            r"risk_review_adjustments\.\d+\.explanation)$"
        )
    )
    label: str = Field(min_length=1, max_length=200)
    stated_value: float = Field(allow_inf_nan=False)
    fraction_digits: int = Field(ge=0, le=8)
    formula: str = Field(min_length=1)
    inputs: tuple[CalculationInputDraft, ...] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    unit: str = Field(min_length=1, max_length=32)
    display_scale: NumericDisplayScale
    display_role: Literal["scalar", "range_low", "range_high"] = "scalar"
    display_group_id: str | None = Field(
        default=None,
        pattern=r"^group_[a-z0-9][a-z0-9_.-]*$",
    )
    limitations: tuple[str, ...] = Field(min_length=1)

    @field_validator("inputs")
    @classmethod
    def validate_unique_inputs(
        cls,
        value: tuple[CalculationInputDraft, ...],
    ) -> tuple[CalculationInputDraft, ...]:
        names = tuple(item.name for item in value)
        if len(names) != len(set(names)):
            raise ValueError("numeric requirement input names must be unique")
        return value

    @model_validator(mode="after")
    def validate_display_group(self) -> DecisionNumericRequirementDraft:
        if self.display_role == "scalar" and self.display_group_id is not None:
            raise ValueError("scalar requirements cannot belong to a range group")
        if self.display_role != "scalar" and self.display_group_id is None:
            raise ValueError("range endpoint requirements require a display group")
        return self


class ResearchScenarioCoreDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ResearchScenarioKind
    core_assumptions: tuple[str, ...] = Field(min_length=1)
    outcome: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class ResearchDecisionCoreDraft(BaseModel):
    """Strict decision fields that must survive optional numeric failures."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rating: ResearchRating
    confidence: float = Field(ge=0.0, le=1.0)
    executive_summary: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    memory_refs: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = Field(min_length=1)
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    unresolved_questions: tuple[str, ...] = ()
    time_horizon: str = Field(min_length=1)
    scenarios: tuple[ResearchScenarioCoreDraft, ...] = Field(
        min_length=3,
        max_length=3,
    )
    risk_review_adjustments: tuple[RiskReviewAdjustment, ...] = ()


class ResearchDecisionCoreEnvelope(ResearchDecisionCoreDraft):
    """Serializer wire envelope with soft numeric annotation candidates."""

    numeric_requirements_declared: bool = False
    numeric_requirement_candidates: tuple[
        SkipValidation[DecisionNumericRequirementDraft], ...
    ] = ()

    @field_serializer("numeric_requirement_candidates", mode="plain")
    def serialize_numeric_requirement_candidates(
        self,
        value: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        return tuple(
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in value
        )

    def qualitative_core(self) -> ResearchDecisionCoreDraft:
        return ResearchDecisionCoreDraft.model_validate(
            self.model_dump(
                exclude={
                    "numeric_requirements_declared",
                    "numeric_requirement_candidates",
                }
            )
        )


@dataclass(frozen=True)
class _NumericRequirementPreflight:
    requirements: tuple[DecisionNumericRequirementDraft, ...]
    issues: tuple[str, ...]
    omissions: tuple[NumericAuditOmission, ...]


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
    """Canonicalize unambiguous Unicode operands before soft schema validation."""

    if isinstance(candidate, BaseModel):
        return candidate
    if not isinstance(candidate, Mapping):
        return candidate
    raw_inputs = candidate.get("inputs")
    formula = candidate.get("formula")
    if not isinstance(raw_inputs, (list, tuple)) or not isinstance(formula, str):
        return candidate
    names = [
        item.get("name") if isinstance(item, Mapping) else None
        for item in raw_inputs
    ]
    if not names or any(not isinstance(name, str) for name in names):
        return candidate
    if all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in names):
        return candidate
    normalized_names = [unicodedata.normalize("NFKC", name) for name in names]
    if len(set(normalized_names)) != len(normalized_names) or any(
        not name.isidentifier() for name in normalized_names
    ):
        return candidate
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError:
        return candidate
    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    if referenced_names != set(normalized_names):
        return candidate
    replacements = {
        name: f"v{index}" for index, name in enumerate(normalized_names, start=1)
    }

    class _OperandRenamer(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.Name:  # noqa: N802
            replacement = replacements.get(node.id)
            return ast.copy_location(ast.Name(id=replacement, ctx=node.ctx), node)

    rewritten = _OperandRenamer().visit(tree)
    ast.fix_missing_locations(rewritten)
    normalized_inputs = [
        {**dict(item), "name": replacements[name]}
        for item, name in zip(raw_inputs, normalized_names, strict=True)
    ]
    return {
        **dict(candidate),
        "formula": ast.unparse(rewritten),
        "inputs": normalized_inputs,
    }


def _preflight_numeric_requirements(
    envelope: ResearchDecisionCoreEnvelope,
    *,
    valid_evidence_refs: set[str],
) -> _NumericRequirementPreflight:
    requirements: list[DecisionNumericRequirementDraft] = []
    issues: list[str] = []
    omissions: list[NumericAuditOmission] = []
    seen_ids: set[str] = set()
    core = envelope.qualitative_core()
    for index, candidate in enumerate(envelope.numeric_requirement_candidates):
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
            continue
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
            continue
        seen_ids.add(requirement.id)
        requirements.append(requirement)
    grouped = {
        group_id: tuple(item for item in requirements if item.display_group_id == group_id)
        for group_id in {
            item.display_group_id
            for item in requirements
            if item.display_group_id is not None
        }
    }
    invalid_group_requirement_ids: set[str] = set()
    for group_id, members in grouped.items():
        roles = {item.display_role for item in members}
        consistent = len(
            {
                (item.component_path, item.unit, item.display_scale)
                for item in members
            }
        ) == 1
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
    return _NumericRequirementPreflight(
        requirements=tuple(requirements),
        issues=tuple(issues),
        omissions=tuple(omissions),
    )


class ObservedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.OBSERVED] = MarketReferenceBasis.OBSERVED
    value_ref: str = Field(pattern=r"^nv_[a-f0-9]{12}$")


class InterpretedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.INTERPRETED] = MarketReferenceBasis.INTERPRETED
    value: float
    anchor_value_refs: tuple[str, ...] = Field(min_length=1)
    context_evidence_refs: tuple[str, ...] = ()

    @field_validator("anchor_value_refs")
    @classmethod
    def validate_anchor_value_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"nv_[a-f0-9]{12}", item) for item in refs):
            raise ValueError("invalid numeric anchor reference")
        return refs


class DerivedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.DERIVED] = MarketReferenceBasis.DERIVED
    calculation_id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")


RangeEndpointDraft: TypeAlias = Annotated[
    ObservedRangeEndpointDraft | InterpretedRangeEndpointDraft | DerivedRangeEndpointDraft,
    Field(discriminator="basis"),
]


class ScenarioReferenceRangeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: ScenarioReferenceCategory
    label: str = Field(min_length=1, max_length=120)
    low: RangeEndpointDraft
    high: RangeEndpointDraft
    interpretation: str = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)


class ScenarioReferenceRangesDraft(BaseModel):
    """Fixed scenario buckets that structurally prevent duplicate kinds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base: tuple[ScenarioReferenceRangeDraft, ...] = ()
    bull: tuple[ScenarioReferenceRangeDraft, ...] = ()
    bear: tuple[ScenarioReferenceRangeDraft, ...] = ()

    def items(
        self,
    ) -> tuple[tuple[ResearchScenarioKind, tuple[ScenarioReferenceRangeDraft, ...]], ...]:
        return (
            (ResearchScenarioKind.BASE, self.base),
            (ResearchScenarioKind.BULL, self.bull),
            (ResearchScenarioKind.BEAR, self.bear),
        )

    def has_content(self) -> bool:
        return bool(self.base or self.bull or self.bear)


class ValuationAssessmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = Field(min_length=1)
    low: DerivedRangeEndpointDraft
    high: DerivedRangeEndpointDraft
    limitations: tuple[str, ...] = Field(min_length=1)


class ObservedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    value_ref: str = Field(pattern=r"^nv_[a-f0-9]{12}$")
    interpretation: str = Field(min_length=1)
    basis: Literal[MarketReferenceBasis.OBSERVED] = MarketReferenceBasis.OBSERVED


class InterpretedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    value: float
    interpretation: str = Field(min_length=1)
    anchor_value_refs: tuple[str, ...] = Field(min_length=1)
    context_evidence_refs: tuple[str, ...] = ()
    basis: Literal[MarketReferenceBasis.INTERPRETED] = MarketReferenceBasis.INTERPRETED

    @field_validator("anchor_value_refs")
    @classmethod
    def validate_anchor_value_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"nv_[a-f0-9]{12}", item) for item in refs):
            raise ValueError("invalid numeric anchor reference")
        return refs


class DerivedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    interpretation: str = Field(min_length=1)
    basis: Literal[MarketReferenceBasis.DERIVED] = MarketReferenceBasis.DERIVED
    calculation_id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")


MarketReferenceLevelDraft: TypeAlias = Annotated[
    ObservedMarketReferenceLevelDraft
    | InterpretedMarketReferenceLevelDraft
    | DerivedMarketReferenceLevelDraft,
    Field(discriminator="basis"),
]


class DecisionNumericDraft(BaseModel):
    """Optional scenario references, valuation, and market-reference payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: bool
    scenario_reference_ranges: ScenarioReferenceRangesDraft = Field(
        default_factory=ScenarioReferenceRangesDraft
    )
    valuation_assessment: ValuationAssessmentDraft | None = None
    market_reference_levels: tuple[MarketReferenceLevelDraft, ...] = ()
    calculation_records: tuple[CalculationRecordDraft, ...] = ()


@dataclass(frozen=True)
class ResearchDecisionOutput:
    value: ResearchDecision
    generation_method: ArtifactGenerationMethod
    warnings: tuple[ResearchWarning, ...] = ()
    numeric_audit: DecisionNumericAuditAppendix | None = None


def write_research_markdown(
    llm: Any,
    *,
    prompt: str,
    node: str,
    allowed_evidence_refs: tuple[str, ...],
    output_language: str,
    invoke_config: dict[str, Any] | None = None,
) -> ResearchMarkdown:
    """Generate one readable deliberation document without a JSON contract."""

    response = llm.invoke(
        prompt + "\n\nWrite the complete research reasoning as readable Markdown. "
        f"Write all human-readable prose in {output_language}. "
        "Use headings, concise tables, and evidence footnotes where they help "
        "the reader. Use only inline `[^ev_xxxxxxxxxxxx]` references and never "
        "write footnote definitions; Evidence Ledger supplies source details. "
        "Do not emit JSON, schema fields, or hidden chain-of-thought.",
        config=invoke_config,
    )
    markdown = _message_text(response).strip()
    if not markdown:
        raise StructuredOutputError(
            node=node,
            schema="ResearchMarkdown",
            reason_code="empty_output",
        )
    if _is_truncated(response):
        continuation = llm.invoke(
            (
                "Continue the prior Markdown from its last complete block. "
                "Do not repeat prior content and finish the document. Write all "
                f"human-readable prose in {output_language}."
            ),
            config=invoke_config,
        )
        if _is_truncated(continuation):
            raise StructuredOutputError(
                node=node,
                schema="ResearchMarkdown",
                reason_code="truncated_output",
            )
        continued = _message_text(continuation).strip()
        if continued:
            markdown = f"{markdown.rstrip()}\n\n{continued}"
    normalized = normalize_evidence_markdown(
        markdown,
        allowed_refs=set(allowed_evidence_refs),
        source=node,
    )
    return ResearchMarkdown(
        markdown=normalized.markdown,
        evidence_refs=normalized.evidence_refs,
        warnings=normalized.warnings,
    )


def invoke_research_case(
    llm: Any,
    *,
    role: str,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[ResearchCase]:
    del llm, state, node, event_writer
    return StructuredOutputResult(
        value=ResearchCase(
            role=role,
            markdown=markdown,
        ),
        generation_method=ArtifactGenerationMethod.MARKDOWN_AUDITED,
    )


def invoke_debate_agenda(
    llm: Any,
    *,
    prompt: str,
    state: Mapping[str, Any],
    node: str,
    output_language: str,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[DebateAgenda]:
    def validate(result: DebateAgenda) -> DebateAgenda:
        require_text(result.summary)
        for issue in result.issues:
            require_text(issue.question)
        return result

    example_text = _agenda_example_text(output_language)
    language_rule = (
        "Write the agenda summary and questions in this complete output-language "
        f"instruction: {output_language}. Keep issue IDs and importance enums in "
        "their required wire format."
    )
    example = DebateAgenda(
        summary=example_text["summary"],
        issues=(
            DebateIssue(
                id="debate.issue_1",
                question=example_text["question"],
                importance=DebateImportance.MATERIAL,
            ),
        ),
    )
    try:
        return _runner(
            llm,
            DebateAgenda,
            validate,
            node,
            event_writer,
            repair_instructions=(
                "Repair only the concise agenda object. Use distinct material "
                f"issues and preserve valid wire IDs. {language_rule}"
            ),
            candidate_only_repair=True,
        ).invoke(
            prompt + "\n\nReturn only a concise agenda summary and distinct material "
            "questions. The full bull and bear reasoning remains in their Markdown. "
            + language_rule
            + "\n\nLOCALIZED VALID EXAMPLE:\n"
            + json.dumps(example.model_dump(mode="json"), ensure_ascii=False),
            example=example.model_dump(mode="json"),
            allowed_evidence_refs=_evidence_refs(state),
        )
    except StructuredOutputError:
        if not _is_standard_output_language(output_language):
            raise
        return StructuredOutputResult(
            value=DebateAgenda(
                summary=example_text["fallback_summary"],
                issues=(
                    DebateIssue(
                        id="debate.issue_audit_fallback",
                        question=example_text["fallback_question"],
                        importance=DebateImportance.MATERIAL,
                    ),
                ),
            ),
            generation_method=(ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE),
        )


def invoke_rebuttal(
    llm: Any,
    *,
    role: str,
    round_number: int,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    conservative_open: bool = False,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[RebuttalReview]:
    agenda = DebateAgenda.model_validate(state["debate_agenda"])
    valid_issues = {issue.id for issue in agenda.issues}

    def validate(result: RebuttalAudit) -> RebuttalAudit:
        addressed = set(result.addressed_issue_ids)
        opened = set(result.open_issue_ids)
        if not addressed.issubset(valid_issues) or not opened.issubset(valid_issues):
            raise OutputValidationError("navigation.issue.unknown")
        if not addressed:
            raise OutputValidationError("navigation.issue.missing_addressed")
        return result

    first_issue = agenda.issues[0].id
    valid_issue_list = tuple(issue.id for issue in agenda.issues)
    try:
        audited = _runner(
            llm,
            RebuttalAudit,
            validate,
            node,
            event_writer,
        ).invoke(
            (
                "Extract only addressed and still-open DebateAgenda issue IDs "
                "from this completed rebuttal. Do not rewrite the Markdown.\n\n"
                f"VALID ISSUE IDS:\n{json.dumps(valid_issue_list)}\n\n"
                f"MARKDOWN:\n{markdown}"
            ),
            example=RebuttalAudit(
                addressed_issue_ids=(first_issue,),
                open_issue_ids=(first_issue,),
            ).model_dump(mode="json"),
            allowed_evidence_refs=_evidence_refs(state),
        )
    except StructuredOutputError:
        mentioned = _mentioned_ids(markdown, valid_issues)
        addressed = mentioned or valid_issue_list
        return StructuredOutputResult(
            value=RebuttalReview(
                role=role,
                round=round_number,
                markdown=markdown,
                addressed_issue_ids=addressed,
                open_issue_ids=valid_issue_list if conservative_open else (),
            ),
            generation_method=(ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE),
        )
    return StructuredOutputResult(
        value=RebuttalReview(
            role=role,
            round=round_number,
            markdown=markdown,
            addressed_issue_ids=audited.value.addressed_issue_ids,
            open_issue_ids=audited.value.open_issue_ids,
        ),
        generation_method=audited.generation_method,
    )


def invoke_judge_draft(
    llm: Any,
    *,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    memory: MemoryContext | None = None,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[JudgeDraft]:
    del memory
    agenda = DebateAgenda.model_validate(state["debate_agenda"])
    issue_ids = {issue.id for issue in agenda.issues}

    def validate(result: JudgeAudit) -> JudgeAudit:
        actual = {item.issue_id for item in result.issue_dispositions}
        if actual != issue_ids:
            raise OutputValidationError("navigation.issue.disposition_incomplete")
        return result

    example_dispositions = tuple(
        IssueDisposition(issue_id=issue.id, status="unresolved") for issue in agenda.issues
    )
    valid_issue_list = tuple(issue.id for issue in agenda.issues)
    try:
        audited = _runner(
            llm,
            JudgeAudit,
            validate,
            node,
            event_writer,
        ).invoke(
            (
                "Extract the preliminary rating, calibrated confidence, and one "
                "routing disposition for every agenda issue from this completed "
                "judge Markdown. Do not rewrite the Markdown.\n\n"
                f"VALID ISSUE IDS:\n{json.dumps(valid_issue_list)}\n\n"
                f"MARKDOWN:\n{markdown}"
            ),
            example=JudgeAudit(
                preliminary_rating=ResearchRating.HOLD,
                confidence=0.55,
                issue_dispositions=example_dispositions,
            ).model_dump(mode="json"),
            allowed_evidence_refs=_evidence_refs(state),
        )
    except StructuredOutputError:
        return StructuredOutputResult(
            value=JudgeDraft(
                markdown=markdown,
                preliminary_rating=None,
                confidence=None,
                issue_dispositions=example_dispositions,
            ),
            generation_method=(ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE),
        )
    return StructuredOutputResult(
        value=JudgeDraft(
            markdown=markdown,
            preliminary_rating=audited.value.preliminary_rating,
            confidence=audited.value.confidence,
            issue_dispositions=audited.value.issue_dispositions,
        ),
        generation_method=audited.generation_method,
    )


def invoke_risk_review(
    llm: Any,
    *,
    role: str,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[RiskReview]:
    del llm, node, event_writer
    agenda = DebateAgenda.model_validate(state["debate_agenda"])
    valid_issues = {issue.id for issue in agenda.issues}
    challenged = _mentioned_ids(markdown, valid_issues)
    unresolved = _mentioned_ids(
        "\n".join(
            line
            for line in markdown.splitlines()
            if re.search(
                r"\b(?:unresolved|open|uncertain)\b|未解决|尚未|不确定",
                line,
                flags=re.IGNORECASE,
            )
        ),
        valid_issues,
    )
    return StructuredOutputResult(
        value=RiskReview(
            role=role,
            markdown=markdown,
            challenged_issue_ids=challenged,
            unresolved_issue_ids=unresolved,
        ),
        generation_method=ArtifactGenerationMethod.MARKDOWN_AUDITED,
    )


def decision_scenario_assumption_guidance(output_language: str) -> str:
    """Return reader-facing scenario guidance without adding a hard validator."""

    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        return (
            "每条情景假设必须在脱离上下文后仍可独立理解。涉及共识、指引、"
            "目标值或预测时，必须写明指标主体（例如 EPS、收入、营业利润或目标价）、"
            "数值与单位，以及理解该假设所需的时间范围或条件。例如："
            "‘分析师 EPS 共识上修至每股 185–195 日元’；不要只写"
            "‘共识上修至 185–195 日元’。"
        )
    if output_language == ReportLanguage.JAPANESE.prompt_label:
        return (
            "各シナリオの前提は、文脈から切り離しても単独で理解できるように書くこと。"
            "コンセンサス、ガイダンス、目標値または予想に触れる場合は、指標の主体"
            "（EPS、売上高、営業利益、目標株価など）、数値と単位、および必要な期間や"
            "条件を明記すること。例：『アナリストのEPSコンセンサスが1株185～195円へ"
            "上方修正される』。『コンセンサスが185～195円へ上方修正される』だけでは"
            "不十分。"
        )
    return (
        "Write every scenario assumption so it remains independently understandable "
        "outside its surrounding context. When referring to consensus, guidance, a "
        "target, or a forecast, name the metric subject (for example EPS, revenue, "
        "operating profit, or target price), its value and unit, and any time period "
        "or condition needed to interpret it. Example: 'Analyst EPS consensus rises "
        "to JPY 185-195 per share'; do not write only 'Consensus rises to JPY 185-195'."
    )


def decision_percentage_calculation_guidance() -> str:
    """Return the stable wire contract for decision percentage calculations."""

    return (
        "For unit %, percent, or pct, formulas must return a fractional ratio in "
        "the 0-to-1 convention and must not multiply by 100; stated_value uses "
        "reader-facing percentage points, and the application deterministically "
        "converts the formula result. For example, "
        "(target_price - close_price) / close_price = 0.4546 uses "
        "stated_value=45.46 and unit=%. A decline formula yielding -0.6132 uses "
        "stated_value=-61.32 and unit=%. Percentage-point formulas also return a "
        "fractional difference and use unit=pp; the application multiplies by 100. "
        "Basis-point formulas return a fractional difference and use unit=bps; the "
        "application multiplies by 10,000. Never multiply these formulas by their "
        "reader-facing scale."
    )


def decision_display_scale_guidance() -> str:
    """Return the canonical contract for compact reader-facing quantities."""

    return (
        "Every numeric requirement must declare display_scale separately from its "
        "canonical unit. Formula inputs and results stay in base units. Use base, "
        "thousand, ten_thousand, million, hundred_million, billion, or trillion. "
        "For example, raw result 80,598,000,000 with unit=USD and "
        "display_scale=hundred_million compares with stated_value=805.98. Do not "
        "encode scale in unit strings such as billion USD, 亿美元, or 百万日元."
    )


def decision_reference_label_guidance(output_language: str) -> str:
    """Return localized naming rules for analyst target references."""

    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        examples = (
            "单个 target_low/min 称为‘目标价下限’，单个 target_high/max 称为"
            "‘目标价上限’，单个 target_mean/average 称为‘目标价均值’。"
        )
    elif output_language == ReportLanguage.JAPANESE.prompt_label:
        examples = (
            "単一の target_low/min は『目標株価下限』、target_high/max は"
            "『目標株価上限』、target_mean/average は『目標株価平均』と呼ぶこと。"
        )
    else:
        examples = (
            "Name a single target_low/min 'analyst target lower bound', a single "
            "target_high/max 'analyst target upper bound', and a single "
            "target_mean/average 'analyst target mean'."
        )
    return (
        examples
        + " Only call an item an analyst target range when it uses two distinct "
        "low and high endpoints. Never duplicate one value ref to preserve a range label."
    )


def _decision_language_rules(output_language: str) -> str:
    return (
        "Write every human-readable field in the requested report language: "
        f"{output_language}. Keep rating values, schema enums, IDs, formula "
        "variable names, Evidence refs, Memory refs, and unit wire values in "
        "their required schema format. "
        + decision_scenario_assumption_guidance(output_language)
    )


def _decision_component_text(
    decision: ResearchDecisionCoreDraft,
    component_path: str,
) -> str | None:
    """Resolve the bounded public field paths accepted by numeric requirements."""

    parts = component_path.split(".")
    if component_path in {"executive_summary", "thesis"}:
        return str(getattr(decision, component_path))
    if parts[0] in {"catalysts", "risks", "invalidation_conditions"} and len(parts) == 2:
        values = getattr(decision, parts[0])
        index = int(parts[1])
        return values[index] if index < len(values) else None
    if parts[0] == "scenarios" and len(parts) in {3, 4}:
        scenario = next(
            (item for item in decision.scenarios if item.kind.value == parts[1]),
            None,
        )
        if scenario is None:
            return None
        if parts[2] == "outcome" and len(parts) == 3:
            return scenario.outcome
        if parts[2] == "core_assumptions" and len(parts) == 4:
            index = int(parts[3])
            return (
                scenario.core_assumptions[index]
                if index < len(scenario.core_assumptions)
                else None
            )
    if (
        parts[0] == "risk_review_adjustments"
        and len(parts) == 3
        and parts[2] == "explanation"
    ):
        index = int(parts[1])
        return (
            decision.risk_review_adjustments[index].explanation
            if index < len(decision.risk_review_adjustments)
            else None
        )
    return None


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
        if any(
            entry.measurement_kind is MeasurementKind.UNKNOWN for entry in resolved
        ):
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


def _decision_example_text(output_language: str) -> dict[str, str]:
    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        return {
            "adjustment_subject": "置信度校准",
            "adjustment_explanation": "最终结论已纳入风险审查意见。",
            "executive_summary": "现有证据支持一项平衡的研究结论。",
            "requirement_thesis": "分析师目标均价对应约 45.5% 的隐含上行空间。",
            "requirement_label": "分析师目标价隐含上行空间",
            "thesis": "该观点取决于一个可验证的经营机制。",
            "risk": "证据支持的下行风险可能会兑现。",
            "invalidation": "新证据直接否定核心论点。",
            "question": "哪一种情景将占据主导？",
            "horizon": "6至12个月",
            "base_assumption": "分析师 EPS 共识维持在每股 185–195 日元。",
            "base_outcome": "核心论点大体按预期演进。",
            "bull_assumption": "未来十二个月分析师 EPS 共识上修至每股 200 日元以上。",
            "bull_outcome": "结果优于基准情景。",
            "bear_assumption": "未来十二个月分析师 EPS 共识下修至每股 175 日元以下。",
            "bear_outcome": "结果弱于基准情景。",
            "valuation_method": "基于证据的盈利倍数法",
            "valuation_limitation": "估值倍数取决于情景假设。",
            "reference_label": "近期观察收盘价",
            "reference_interpretation": "这是直接观察的参考值，并非执行指令。",
            "scenario_range_label": "技术参考区间",
            "scenario_range_interpretation": "该区间来自已观察的市场位置，并非估值结论。",
        }
    if output_language == ReportLanguage.JAPANESE.prompt_label:
        return {
            "adjustment_subject": "確信度の調整",
            "adjustment_explanation": "最終判断にはリスクレビューを反映した。",
            "executive_summary": "現時点の証拠は均衡の取れた判断を支持する。",
            "requirement_thesis": "アナリスト平均目標株価は約45.5%の上昇余地を示す。",
            "requirement_label": "アナリスト目標株価の上昇余地",
            "thesis": "この見解は検証可能な事業メカニズムに依存する。",
            "risk": "証拠に裏付けられた下振れリスクが顕在化し得る。",
            "invalidation": "新たな証拠が中核仮説を直接否定する。",
            "question": "どのシナリオが優勢になるか。",
            "horizon": "6〜12か月",
            "base_assumption": "アナリストのEPSコンセンサスが1株185～195円で維持される。",
            "base_outcome": "仮説は概ね想定どおりに進展する。",
            "bull_assumption": "今後12か月のEPSコンセンサスが1株200円超へ上方修正される。",
            "bull_outcome": "結果は基本シナリオを上回る。",
            "bear_assumption": "今後12か月のEPSコンセンサスが1株175円未満へ下方修正される。",
            "bear_outcome": "結果は基本シナリオを下回る。",
            "valuation_method": "証拠に基づく利益倍率法",
            "valuation_limitation": "倍率はシナリオ前提に左右される。",
            "reference_label": "直近の観測終値",
            "reference_interpretation": "直接観測した参考値であり、執行指示ではない。",
            "scenario_range_label": "テクニカル参考レンジ",
            "scenario_range_interpretation": "観測済みの市場水準であり、企業価値評価ではない。",
        }
    return {
        "adjustment_subject": "Confidence calibration",
        "adjustment_explanation": "The final decision incorporates the risk review.",
        "executive_summary": "The evidence supports a balanced conclusion.",
        "requirement_thesis": "The analyst mean target implies about 45.5% upside.",
        "requirement_label": "Analyst target implied upside",
        "thesis": "The view depends on a testable operating mechanism.",
        "risk": "The evidence-backed downside may materialize.",
        "invalidation": "New evidence directly contradicts the thesis.",
        "question": "Which scenario will dominate?",
        "horizon": "6-12 months",
        "base_assumption": "Analyst EPS consensus remains JPY 185-195 per share.",
        "base_outcome": "The thesis develops broadly as expected.",
        "bull_assumption": "Twelve-month analyst EPS consensus rises above JPY 200 per share.",
        "bull_outcome": "The result exceeds the base case.",
        "bear_assumption": "Twelve-month analyst EPS consensus falls below JPY 175 per share.",
        "bear_outcome": "The result falls below the base case.",
        "valuation_method": "Evidence-backed earnings multiple",
        "valuation_limitation": "The multiple is scenario-dependent.",
        "reference_label": "Observed recent close",
        "reference_interpretation": ("A directly observed reference, not an execution order."),
        "scenario_range_label": "Technical reference range",
        "scenario_range_interpretation": (
            "The range uses observed market levels and is not a valuation conclusion."
        ),
    }


def _numeric_example_pair(
    value_catalog: tuple[NumericValueCatalogEntry, ...],
) -> tuple[NumericValueCatalogEntry, NumericValueCatalogEntry] | None:
    """Return one compatible, strictly ordered pair for the prompt example."""

    for index, first in enumerate(value_catalog):
        for second in value_catalog[index + 1 :]:
            if (
                first.measurement_kind is not second.measurement_kind
                or first.unit != second.unit
                or first.value == second.value
            ):
                continue
            return tuple(sorted((first, second), key=lambda item: item.value))
    return None


def invoke_research_decision(
    llm: Any,
    *,
    numeric_llm: Any | None = None,
    prompt: str,
    state: Mapping[str, Any],
    node: str,
    memory: MemoryContext | None = None,
    require_risk_adjustments: bool,
    event_writer: EventWriter | None = None,
    output_language: str | None = None,
    metrics: Any | None = None,
) -> ResearchDecisionOutput:
    numeric_output_llm = llm if numeric_llm is None else numeric_llm
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    valid_refs = tuple(item.ref for item in bundle.items)
    valid_memory_refs = tuple(memory.refs if memory is not None else ())
    first_ref = valid_refs[0]
    risk_roles = tuple(state.get("risk_reviews", {}))
    resolved_language = output_language or str(
        state.get("output_language") or ReportLanguage.ENGLISH.prompt_label
    )
    example_text = _decision_example_text(resolved_language)
    language_rules = _decision_language_rules(resolved_language)
    percentage_rules = decision_percentage_calculation_guidance()
    display_scale_rules = decision_display_scale_guidance()
    example_adjustments = (
        (
            RiskReviewAdjustment(
                source_role=risk_roles[0],
                disposition=RiskReviewDisposition.MODIFIED,
                subject=example_text["adjustment_subject"],
                explanation=example_text["adjustment_explanation"],
                evidence_refs=(first_ref,),
            ),
        )
        if risk_roles
        else ()
    )

    def validate_core(
        result: ResearchDecisionCoreEnvelope,
    ) -> ResearchDecisionCoreEnvelope:
        scenario_kinds = tuple(item.kind for item in result.scenarios)
        if len(set(scenario_kinds)) != len(scenario_kinds):
            raise OutputValidationError("decision.scenarios.duplicate_kind")
        if set(scenario_kinds) != set(ResearchScenarioKind):
            raise OutputValidationError("decision.scenarios.incomplete_set")
        require_text(result.executive_summary)
        require_text(result.thesis)
        require_nonempty_texts(result.risks)
        require_nonempty_texts(result.invalidation_conditions)
        require_text(result.time_horizon)
        require_valid_refs(result.evidence_refs, set(valid_refs), required=True)
        require_valid_refs(
            result.memory_refs,
            set(valid_memory_refs),
            required=False,
        )
        for scenario in result.scenarios:
            require_nonempty_texts(scenario.core_assumptions)
            require_text(scenario.outcome)
            require_valid_refs(
                scenario.evidence_refs,
                set(valid_refs),
                required=True,
            )
        if require_risk_adjustments:
            adjusted_roles = {item.source_role for item in result.risk_review_adjustments}
            if not set(risk_roles).issubset(adjusted_roles):
                raise OutputValidationError("decision.risk_review.missing_role")
        if any(item.source_role not in risk_roles for item in result.risk_review_adjustments):
            raise OutputValidationError("decision.risk_review.unknown_role")
        for adjustment in result.risk_review_adjustments:
            require_text(adjustment.subject)
            require_text(adjustment.explanation)
            require_valid_refs(
                adjustment.evidence_refs,
                set(valid_refs),
                required=False,
            )
        return result

    core_example = ResearchDecisionCoreEnvelope(
        rating=ResearchRating.HOLD,
        confidence=0.5,
        executive_summary=example_text["executive_summary"],
        thesis=example_text["requirement_thesis"],
        evidence_refs=(first_ref,),
        risks=(example_text["risk"],),
        invalidation_conditions=(example_text["invalidation"],),
        unresolved_questions=(example_text["question"],),
        time_horizon=example_text["horizon"],
        scenarios=(
            ResearchScenarioCoreDraft(
                kind=ResearchScenarioKind.BASE,
                core_assumptions=(example_text["base_assumption"],),
                outcome=example_text["base_outcome"],
                evidence_refs=(first_ref,),
            ),
            ResearchScenarioCoreDraft(
                kind=ResearchScenarioKind.BULL,
                core_assumptions=(example_text["bull_assumption"],),
                outcome=example_text["bull_outcome"],
                evidence_refs=(first_ref,),
            ),
            ResearchScenarioCoreDraft(
                kind=ResearchScenarioKind.BEAR,
                core_assumptions=(example_text["bear_assumption"],),
                outcome=example_text["bear_outcome"],
                evidence_refs=(first_ref,),
            ),
        ),
        risk_review_adjustments=example_adjustments,
        numeric_requirements_declared=True,
        numeric_requirement_candidates=(
            DecisionNumericRequirementDraft(
                id="req_example_percentage",
                component_path="thesis",
                label=example_text["requirement_label"],
                stated_value=45.5,
                fraction_digits=1,
                formula="(target_price - close_price) / close_price",
                inputs=(
                    CalculationInputDraft(
                        name="target_price",
                        value=145.5,
                        date_evidence_refs=(first_ref,),
                    ),
                    CalculationInputDraft(
                        name="close_price",
                        value=100,
                        date_evidence_refs=(first_ref,),
                    ),
                ),
                input_evidence_refs=(first_ref,),
                unit="%",
                display_scale=NumericDisplayScale.BASE,
                limitations=(example_text["valuation_limitation"],),
            ),
        ),
    )
    core_node = f"{node}.core"
    core_phase = (
        metrics.phase(core_node, event_writer=event_writer)
        if metrics is not None
        else nullcontext()
    )
    with core_phase:
        core = StructuredOutputRunner(
            llm=llm,
            schema=ResearchDecisionCoreEnvelope,
            validator=validate_core,
            node=core_node,
            event_writer=event_writer,
            repair_mode="preferred",
            include_candidate_in_repair=True,
            candidate_only_repair=True,
            invoke_config={"metadata": {"research_node": core_node}},
            repair_instructions=(
                "Keep valid research content. Use only allowed evidence and memory "
                "refs. Do not include valuation ranges, market-reference levels, "
                "or optional numeric components in this core object. Register every "
                "decision-critical derived exact number in "
                "numeric_requirement_candidates and set "
                "numeric_requirements_declared accordingly; "
                "directly observed Evidence values need no requirement. Candidate "
                "inputs must be an array of {name, value, date_evidence_refs} objects, "
                "never a dynamic mapping. Each observed input lists only the Evidence "
                "refs that establish its date; pure constants use an empty list. "
                "Limitations must be an array of strings. Every "
                "component_path must identify one exact core field such as risks.0 "
                "or catalysts.0 or scenarios.base.core_assumptions.2; omit an uncertain annotation "
                "instead of using a coarse path such as risks or scenarios. "
                "Do not create requirements for unresolved_questions. A displayed "
                "derived range requires two requirements with the same display_group_id "
                "and distinct range_low/range_high display_role values; never attach "
                "one scalar requirement to two calculations. "
                f"{percentage_rules} {display_scale_rules} "
                "scenarios must contain "
                "exactly one base, one bull, and one bear case. Required "
                f"risk-review roles: {json.dumps(risk_roles)}. {language_rules}"
            ),
        ).invoke(
            prompt + "\n\nSerialize only the strict qualitative decision core. Numeric "
            "valuation, scenario ranges, market reference levels, and canonical "
            "calculations are handled by a separate audit step. Preserve the brief's "
            "decision-critical calculation checklist as soft "
            "numeric_requirement_candidates. These annotations do not replace "
            "the strict qualitative fields. Candidate inputs are arrays of named "
            "values, limitations are string arrays, and component paths point to "
            "specific indexed core fields. Each formula input supplies "
            "date_evidence_refs for the Evidence that dates that input; explanatory "
            "background refs do not belong there. Omit a candidate when its exact core "
            "location cannot be identified. Audit decision-critical derived values "
            "in catalysts, but do not annotate unresolved questions. Use paired "
            "range_low/range_high requirements with one display_group_id for a "
            "derived range. "
            + percentage_rules
            + " "
            + display_scale_rules
            + " "
            + language_rules
            + "\n\nLOCALIZED VALID EXAMPLE:\n"
            + json.dumps(core_example.model_dump(mode="json"), ensure_ascii=False),
            example=core_example.model_dump(mode="json"),
            allowed_evidence_refs=valid_refs,
            allowed_memory_refs=valid_memory_refs,
        )

    core_envelope = core.value
    core_value = core_envelope.qualitative_core()
    requirement_preflight = _preflight_numeric_requirements(
        core_envelope,
        valid_evidence_refs=set(valid_refs),
    )
    numeric_node = f"{node}.numeric"
    numeric_phase = (
        metrics.phase(numeric_node, event_writer=event_writer)
        if metrics is not None
        else nullcontext()
    )
    with numeric_phase:
        numeric = _invoke_decision_numeric(
            numeric_output_llm,
            prompt=prompt,
            node=numeric_node,
            bundle=bundle,
            allowed_evidence_refs=valid_refs,
            event_writer=event_writer,
            output_language=resolved_language,
            core_scenarios=core_value.scenarios,
            requirements=requirement_preflight.requirements,
        )
    numeric = _apply_requirement_preflight(
        numeric,
        requirement_preflight,
        node=numeric_node,
        event_writer=event_writer,
    )
    scenario_values = []
    for scenario in core_value.scenarios:
        numeric_scenarios = numeric.scenario_reference_ranges.get(scenario.kind, ())
        scenario_values.append(
            ResearchScenario(
                kind=scenario.kind,
                core_assumptions=scenario.core_assumptions,
                outcome=scenario.outcome,
                evidence_refs=scenario.evidence_refs,
                reference_ranges=numeric_scenarios,
            )
        )
    decision = ResearchDecision(
        rating=core_value.rating,
        confidence=core_value.confidence,
        executive_summary=core_value.executive_summary,
        thesis=core_value.thesis,
        evidence_refs=core_value.evidence_refs,
        memory_refs=core_value.memory_refs,
        catalysts=core_value.catalysts,
        risks=core_value.risks,
        invalidation_conditions=core_value.invalidation_conditions,
        unresolved_questions=core_value.unresolved_questions,
        time_horizon=core_value.time_horizon,
        scenarios=tuple(scenario_values),
        valuation_assessment=numeric.valuation_assessment,
        market_reference_levels=numeric.market_reference_levels,
        calculation_records=numeric.calculation_records,
        risk_review_adjustments=core_value.risk_review_adjustments,
        numeric_audit_status=numeric.status,
    )
    require_valid_refs(
        decision.evidence_refs,
        set(valid_refs),
        required=True,
    )
    require_valid_refs(
        decision.memory_refs,
        set(valid_memory_refs),
        required=False,
    )
    return ResearchDecisionOutput(
        value=decision,
        generation_method=core.generation_method,
        warnings=numeric.warnings,
        numeric_audit=numeric.audit,
    )


@dataclass(frozen=True)
class _NumericDecisionAssembly:
    scenario_reference_ranges: dict[ResearchScenarioKind, tuple[ScenarioReferenceRange, ...]]
    valuation_assessment: ValuationAssessment | None
    market_reference_levels: tuple[MarketReferenceLevel, ...]
    calculation_records: tuple[CalculationRecord, ...]
    status: NumericAuditStatus
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
    omissions = tuple(
        dict.fromkeys((*assembly.omissions, *preflight.omissions))
    )
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
                    *(
                        assembly.audit.omitted_components
                        if assembly.audit is not None
                        else ()
                    ),
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
        draft = _numeric_candidate(exc.candidate)
        if draft is None:
            omissions = _requirement_omissions(
                requirements,
                issue_suffix="missing_calculation",
            )
            empty = _empty_numeric_assembly(
                node=node,
                status=(
                    NumericAuditStatus.PARTIAL
                    if requirements
                    else NumericAuditStatus.INCOMPLETE
                ),
                requirement_checks=_missing_requirement_checks(
                    requirements,
                    issue_suffix="missing_calculation",
                ),
            )
            return _emit_numeric_normalization_event(
                replace(
                    empty,
                    audit=_numeric_audit_appendix(
                        status=NumericAuditAppendixStatus.INCOMPLETE,
                        failures=exc.failures,
                        omissions=omissions or (
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
    if isinstance(low, ObservedRangeEndpointDraft) and isinstance(
        high, ObservedRangeEndpointDraft
    ):
        return low.value_ref == high.value_ref
    if isinstance(low, InterpretedRangeEndpointDraft) and isinstance(
        high, InterpretedRangeEndpointDraft
    ):
        return (
            low.value == high.value
            and set(low.anchor_value_refs) == set(high.anchor_value_refs)
            and set(low.context_evidence_refs) == set(high.context_evidence_refs)
        )
    if isinstance(low, DerivedRangeEndpointDraft) and isinstance(
        high, DerivedRangeEndpointDraft
    ):
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
        input_evidence_refs=requirement.input_evidence_refs,
        date_evidence_refs=_calculation_date_refs(requirement.inputs),
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
            issue_codes=(
                f"numeric.requirement.{requirement.id}.{issue_suffix}",
            ),
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
                repair_issues.append(
                    f"numeric.calculation.{calculation_id}.unknown_requirement"
                )
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
            elif _calculation_date_refs(item.inputs) != _calculation_date_refs(
                requirement.inputs
            ):
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
                    raw_result = Decimal(
                        str(raw_calculation_results[calculation_id])
                    ).quantize(
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
                            comparison_difference=(
                                comparison_result - requirement.stated_value
                            ),
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
                        comparison_difference=(
                            comparison_result - requirement.stated_value
                        ),
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
                    entry.measurement_kind is MeasurementKind.UNKNOWN
                    for entry in anchor_entries
                ):
                    measurement_kind = MeasurementKind.UNKNOWN
                    unit = None
                else:
                    measurements = {
                        (entry.measurement_kind, entry.unit)
                        for entry in anchor_entries
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
        requirement_checks[item.id]
        for item in requirements
        if item.id in requirement_checks
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


def debate_round_has_material_progress(
    state: Mapping[str, Any],
    *,
    round_number: int,
) -> bool:
    """Continue only when the set of material open issues actually changes."""

    rebuttals = [RebuttalReview.model_validate(raw) for raw in state.get("rebuttals", [])]
    current = [item for item in rebuttals if item.round == round_number]
    if not current:
        return False
    current_open = {issue_id for item in current for issue_id in item.open_issue_ids}
    if not current_open:
        return False
    prior = [item for item in rebuttals if item.round < round_number]
    if not prior:
        return True
    prior_open = {issue_id for item in prior for issue_id in item.open_issue_ids}
    return current_open != prior_open


def _runner(
    llm: Any,
    schema: Any,
    validator: Callable[[Any], Any],
    node: str,
    event_writer: EventWriter | None,
    repair_instructions: str | None = None,
    *,
    candidate_only_repair: bool = False,
) -> StructuredOutputRunner[Any]:
    return StructuredOutputRunner(
        llm=llm,
        schema=schema,
        validator=validator,
        node=node,
        event_writer=event_writer,
        invoke_config={"metadata": {"research_node": node}},
        repair_mode="preferred",
        include_candidate_in_repair=True,
        candidate_only_repair=candidate_only_repair,
        repair_instructions=repair_instructions
        or (
            "Repair only invalid shallow routing metadata such as issue IDs, "
            "confidence, or dispositions. The readable Markdown is already "
            "complete and must not be regenerated."
        ),
    )


def _agenda_example_text(output_language: str) -> dict[str, str]:
    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        return {
            "summary": "多空案例对一个重要经营机制存在分歧。",
            "question": "有争议的经营机制能否持续？",
            "fallback_summary": "已完成的多空案例存在重要分歧，但议程导航审计不完整。",
            "fallback_question": "多空案例之间仍未解决的核心分歧是什么？",
        }
    if output_language == ReportLanguage.JAPANESE.prompt_label:
        return {
            "summary": "強気・弱気ケースは重要な事業メカニズムについて対立している。",
            "question": "争点となる事業メカニズムは持続するか。",
            "fallback_summary": "強気・弱気ケースには重要な対立があるが、議題監査は不完全である。",
            "fallback_question": "両ケース間で未解決の重要な対立は何か。",
        }
    return {
        "summary": "The cases disagree on one material operating mechanism.",
        "question": "Will the disputed operating mechanism persist?",
        "fallback_summary": (
            "The completed bull and bear cases contain a material disagreement "
            "whose agenda audit is incomplete."
        ),
        "fallback_question": (
            "Which material disagreement between the completed cases remains unresolved?"
        ),
    }


def _is_standard_output_language(output_language: str) -> bool:
    return output_language in {item.prompt_label for item in ReportLanguage}


def _evidence_refs(state: Mapping[str, Any]) -> tuple[str, ...]:
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    return tuple(item.ref for item in bundle.items)


def _mentioned_ids(markdown: str, valid_ids: set[str]) -> tuple[str, ...]:
    """Return exact valid IDs in first-appearance order."""

    matches: list[tuple[int, str]] = []
    for candidate in valid_ids:
        match = re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(candidate)}"
            r"(?![A-Za-z0-9_-])",
            markdown,
        )
        if match is not None:
            matches.append((match.start(), candidate))
    return tuple(candidate for _, candidate in sorted(matches))


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _is_truncated(response: Any) -> bool:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict) and isinstance(response, dict):
        metadata = response.get("response_metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get("finish_reason") in {
        "length",
        "max_tokens",
        "max_output_tokens",
    }


def requirement_input_mapping(
    requirement: DecisionNumericRequirementDraft,
) -> dict[str, int | float]:
    return {item.name: item.value for item in requirement.inputs}


def _calculation_date_refs(
    inputs: tuple[CalculationInputDraft, ...],
) -> tuple[str, ...]:
    """Return only Evidence refs that date required formula inputs."""

    return tuple(
        dict.fromkeys(
            evidence_ref
            for item in inputs
            for evidence_ref in item.date_evidence_refs
        )
    )


def _formula_identity(formula: str) -> str | None:
    try:
        return ast.dump(ast.parse(formula, mode="eval"), include_attributes=False)
    except SyntaxError:
        return None


def _evaluate_formula(
    formula: str,
    inputs: Mapping[str, int | float],
    *,
    issue_prefix: str = "calculation",
) -> float:
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise OutputValidationError(f"{issue_prefix}.formula.invalid_syntax") from exc

    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    missing_names = referenced_names.difference(inputs)
    if missing_names:
        raise OutputValidationError(f"{issue_prefix}.formula.missing_input")
    unused_names = set(inputs).difference(referenced_names)
    if unused_names:
        raise OutputValidationError(f"{issue_prefix}.formula.unused_input")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value,
                (int, float),
            ):
                raise OutputValidationError(f"{issue_prefix}.formula.non_numeric_constant")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in inputs:
                raise OutputValidationError(f"{issue_prefix}.formula.missing_input")
            return float(inputs[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op,
            (ast.UAdd, ast.USub),
        ):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise OutputValidationError(f"{issue_prefix}.formula.division_by_zero")
                return left / right
            if isinstance(node.op, ast.Pow) and abs(right) <= 12:
                try:
                    return left**right
                except OverflowError as exc:
                    raise OutputValidationError(f"{issue_prefix}.formula.overflow") from exc
        raise OutputValidationError(f"{issue_prefix}.formula.unsupported_operation")

    result = evaluate(tree)
    if not math.isfinite(result):
        raise OutputValidationError(f"{issue_prefix}.formula.non_finite_result")
    return result


_PERCENT_CALCULATION_UNITS = {"%", "PCT", "PERCENT"}
_PERCENTAGE_POINT_CALCULATION_UNITS = {"PP", "PERCENTAGE POINTS"}
_BASIS_POINT_CALCULATION_UNITS = {"BPS", "BASIS POINTS"}

_DISPLAY_SCALE_FACTORS = {
    NumericDisplayScale.BASE: Decimal("1"),
    NumericDisplayScale.THOUSAND: Decimal("1000"),
    NumericDisplayScale.TEN_THOUSAND: Decimal("10000"),
    NumericDisplayScale.MILLION: Decimal("1000000"),
    NumericDisplayScale.HUNDRED_MILLION: Decimal("100000000"),
    NumericDisplayScale.BILLION: Decimal("1000000000"),
    NumericDisplayScale.TRILLION: Decimal("1000000000000"),
}


def _is_percent_calculation_unit(unit: str) -> bool:
    return unit.strip().upper() in _PERCENT_CALCULATION_UNITS


def _is_ratio_scaled_calculation_unit(unit: str) -> bool:
    return unit.strip().upper() in (
        _PERCENT_CALCULATION_UNITS
        | _PERCENTAGE_POINT_CALCULATION_UNITS
        | _BASIS_POINT_CALCULATION_UNITS
    )


def _display_values_approximately_match(
    *,
    stated_value: Decimal,
    comparison_value: Decimal,
    raw_stated_value: Decimal,
    raw_comparison_value: Decimal,
    quantum: Decimal,
) -> bool:
    if raw_stated_value and raw_comparison_value and (
        raw_stated_value.is_signed() != raw_comparison_value.is_signed()
    ):
        return False
    rounded_difference = abs(stated_value - comparison_value)
    relative_base = max(abs(raw_stated_value), abs(raw_comparison_value), Decimal("1"))
    relative_difference = abs(raw_stated_value - raw_comparison_value) / relative_base
    return rounded_difference <= quantum and relative_difference <= Decimal("0.01")


def _canonicalize_calculation_result(
    result: float,
    unit: str,
    *,
    issue_prefix: str = "calculation",
) -> float:
    """Convert a safe formula result into the public unit's canonical value."""

    normalized_unit = unit.strip().upper()
    if normalized_unit in (
        _PERCENT_CALCULATION_UNITS | _PERCENTAGE_POINT_CALCULATION_UNITS
    ):
        canonical = result * 100
    elif normalized_unit in _BASIS_POINT_CALCULATION_UNITS:
        canonical = result * 10_000
    else:
        canonical = result
    if not math.isfinite(canonical):
        raise OutputValidationError(f"{issue_prefix}.formula.non_finite_result")
    return canonical


def _scale_for_display(
    result: int | float,
    scale: NumericDisplayScale,
) -> float:
    value = Decimal(str(result)) / _DISPLAY_SCALE_FACTORS[scale]
    if not value.is_finite():
        raise OutputValidationError("calculation.display_scale.non_finite")
    return float(value)
