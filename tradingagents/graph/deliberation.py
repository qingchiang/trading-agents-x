"""Readable research deliberation with shallow routing contracts."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    AuditedRangeEndpoint,
    CalculationRecord,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    DecisionNumericAuditAppendix,
    EvidenceBundle,
    EvidenceItem,
    EvidenceTemporalScope,
    EvidenceValueLocator,
    IssueDisposition,
    JudgeDraft,
    MarketReferenceBasis,
    MarketReferenceLevel,
    MemoryContext,
    NumericAuditAppendixStatus,
    NumericAuditComponentType,
    NumericAuditOmission,
    NumericAuditPhase,
    NumericAuditSnapshot,
    NumericAuditStatus,
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


class CalculationRecordDraft(BaseModel):
    """Serializer-facing calculation without dynamic JSON object keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")
    formula: str = Field(min_length=1)
    inputs: tuple[CalculationInputDraft, ...] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    unit: str = Field(min_length=1, max_length=32)
    limitations: tuple[str, ...] = Field(min_length=1)

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


class ObservedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.OBSERVED] = MarketReferenceBasis.OBSERVED
    value_ref: str = Field(pattern=r"^nv_[a-f0-9]{12}$")


class InterpretedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.INTERPRETED] = (
        MarketReferenceBasis.INTERPRETED
    )
    value: float
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class DerivedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.DERIVED] = MarketReferenceBasis.DERIVED
    calculation_id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")


RangeEndpointDraft: TypeAlias = Annotated[
    ObservedRangeEndpointDraft
    | InterpretedRangeEndpointDraft
    | DerivedRangeEndpointDraft,
    Field(discriminator="basis"),
]


class ScenarioReferenceRangeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: ScenarioReferenceCategory
    label: str = Field(min_length=1, max_length=120)
    low: RangeEndpointDraft
    high: RangeEndpointDraft
    unit: str = Field(min_length=1, max_length=32)
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
    ) -> tuple[
        tuple[ResearchScenarioKind, tuple[ScenarioReferenceRangeDraft, ...]], ...
    ]:
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
    currency: str = Field(min_length=1, max_length=16)
    limitations: tuple[str, ...] = Field(min_length=1)


class ObservedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    value_ref: str = Field(pattern=r"^nv_[a-f0-9]{12}$")
    unit: str = Field(min_length=1, max_length=32)
    interpretation: str = Field(min_length=1)
    basis: Literal[MarketReferenceBasis.OBSERVED] = MarketReferenceBasis.OBSERVED


class InterpretedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    value: float
    unit: str = Field(min_length=1, max_length=32)
    interpretation: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    basis: Literal[MarketReferenceBasis.INTERPRETED] = (
        MarketReferenceBasis.INTERPRETED
    )


class DerivedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=32)
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


def _decision_language_rules(output_language: str) -> str:
    return (
        "Write every human-readable field in the requested report language: "
        f"{output_language}. Keep rating values, schema enums, IDs, formula "
        "variable names, Evidence refs, Memory refs, and unit wire values in "
        "their required schema format."
    )


_SCENARIO_LABEL_PATTERNS: dict[
    ReportLanguage,
    dict[ResearchScenarioKind, tuple[str, ...]],
] = {
    ReportLanguage.ENGLISH: {
        ResearchScenarioKind.BASE: (r"\b(?:base|neutral)\s+(?:scenario|case)\b",),
        ResearchScenarioKind.BULL: (
            r"\b(?:bull|bullish|upside|recovery)\s+(?:scenario|case)\b",
        ),
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


def _label_declares_other_scenario(
    label: str,
    *,
    owner: ResearchScenarioKind,
    output_language: str,
) -> bool:
    language = next(
        (
            candidate
            for candidate in ReportLanguage
            if output_language == candidate.prompt_label
        ),
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


def _decision_example_text(output_language: str) -> dict[str, str]:
    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        return {
            "adjustment_subject": "置信度校准",
            "adjustment_explanation": "最终结论已纳入风险审查意见。",
            "executive_summary": "现有证据支持一项平衡的研究结论。",
            "thesis": "该观点取决于一个可验证的经营机制。",
            "risk": "证据支持的下行风险可能会兑现。",
            "invalidation": "新证据直接否定核心论点。",
            "question": "哪一种情景将占据主导？",
            "horizon": "6至12个月",
            "base_assumption": "当前证据仍具有代表性。",
            "base_outcome": "核心论点大体按预期演进。",
            "bull_assumption": "建设性机制进一步增强。",
            "bull_outcome": "结果优于基准情景。",
            "bear_assumption": "主要风险开始兑现。",
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
            "thesis": "この見解は検証可能な事業メカニズムに依存する。",
            "risk": "証拠に裏付けられた下振れリスクが顕在化し得る。",
            "invalidation": "新たな証拠が中核仮説を直接否定する。",
            "question": "どのシナリオが優勢になるか。",
            "horizon": "6〜12か月",
            "base_assumption": "現在の証拠が引き続き代表性を持つ。",
            "base_outcome": "仮説は概ね想定どおりに進展する。",
            "bull_assumption": "上振れメカニズムが強まる。",
            "bull_outcome": "結果は基本シナリオを上回る。",
            "bear_assumption": "主要リスクが顕在化する。",
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
        "thesis": "The view depends on a testable operating mechanism.",
        "risk": "The evidence-backed downside may materialize.",
        "invalidation": "New evidence directly contradicts the thesis.",
        "question": "Which scenario will dominate?",
        "horizon": "6-12 months",
        "base_assumption": "Current evidence remains representative.",
        "base_outcome": "The thesis develops broadly as expected.",
        "bull_assumption": "The constructive mechanism strengthens.",
        "bull_outcome": "The result exceeds the base case.",
        "bear_assumption": "The principal risk materializes.",
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


def invoke_research_decision(
    llm: Any,
    *,
    prompt: str,
    state: Mapping[str, Any],
    node: str,
    memory: MemoryContext | None = None,
    require_risk_adjustments: bool,
    event_writer: EventWriter | None = None,
    output_language: str | None = None,
    metrics: Any | None = None,
) -> ResearchDecisionOutput:
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
        result: ResearchDecisionCoreDraft,
    ) -> ResearchDecisionCoreDraft:
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
        return result

    core_example = ResearchDecisionCoreDraft(
        rating=ResearchRating.HOLD,
        confidence=0.5,
        executive_summary=example_text["executive_summary"],
        thesis=example_text["thesis"],
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
            schema=ResearchDecisionCoreDraft,
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
                "or calculations in this core object. The scenarios must contain "
                "exactly one base, one bull, and one bear case. Required "
                f"risk-review roles: {json.dumps(risk_roles)}. {language_rules}"
            ),
        ).invoke(
            prompt + "\n\nSerialize only the strict qualitative decision core. Numeric "
            "valuation, scenario ranges, market reference levels, and calculations "
            "are handled by a separate audit step. "
            + language_rules
            + "\n\nLOCALIZED VALID EXAMPLE:\n"
            + json.dumps(core_example.model_dump(mode="json"), ensure_ascii=False),
            example=core_example.model_dump(mode="json"),
            allowed_evidence_refs=valid_refs,
            allowed_memory_refs=valid_memory_refs,
        )

    core_value = core.value
    numeric_node = f"{node}.numeric"
    numeric_phase = (
        metrics.phase(numeric_node, event_writer=event_writer)
        if metrics is not None
        else nullcontext()
    )
    with numeric_phase:
        numeric = _invoke_decision_numeric(
            llm,
            prompt=prompt,
            node=numeric_node,
            bundle=bundle,
            allowed_evidence_refs=valid_refs,
            event_writer=event_writer,
            output_language=resolved_language,
            core_scenarios=core_value.scenarios,
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
    return ResearchDecisionOutput(
        value=decision,
        generation_method=core.generation_method,
        warnings=numeric.warnings,
        numeric_audit=numeric.audit,
    )


@dataclass(frozen=True)
class _NumericDecisionAssembly:
    scenario_reference_ranges: dict[
        ResearchScenarioKind, tuple[ScenarioReferenceRange, ...]
    ]
    valuation_assessment: ValuationAssessment | None
    market_reference_levels: tuple[MarketReferenceLevel, ...]
    calculation_records: tuple[CalculationRecord, ...]
    status: NumericAuditStatus
    warnings: tuple[ResearchWarning, ...] = ()
    issues: tuple[str, ...] = ()
    omissions: tuple[NumericAuditOmission, ...] = ()
    audit: DecisionNumericAuditAppendix | None = None


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
) -> _NumericDecisionAssembly:
    allowed = set(allowed_evidence_refs)
    value_catalog = build_numeric_value_catalog(
        bundle,
        allowed_evidence_refs=allowed,
    )
    value_catalog_by_id = {item.id: item for item in value_catalog}
    example_text = _decision_example_text(output_language)
    language_rules = _decision_language_rules(output_language)
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

    def validate(draft: DecisionNumericDraft) -> DecisionNumericDraft:
        _assemble_numeric_draft(
            draft,
            bundle=bundle,
            allowed_evidence_refs=allowed,
            value_catalog=value_catalog_by_id,
            salvage=False,
            node=node,
            output_language=output_language,
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

    example_low: RangeEndpointDraft
    example_high: RangeEndpointDraft
    example_reference: MarketReferenceLevelDraft
    if value_catalog:
        example_low = ObservedRangeEndpointDraft(value_ref=value_catalog[0].id)
        example_high = ObservedRangeEndpointDraft(value_ref=value_catalog[0].id)
        example_reference = ObservedMarketReferenceLevelDraft(
            label=example_text["reference_label"],
            value_ref=value_catalog[0].id,
            unit=value_catalog[0].unit or "USD",
            interpretation=example_text["reference_interpretation"],
        )
    else:
        example_low = InterpretedRangeEndpointDraft(
            value=95,
            evidence_refs=(allowed_evidence_refs[0],),
        )
        example_high = InterpretedRangeEndpointDraft(
            value=105,
            evidence_refs=(allowed_evidence_refs[0],),
        )
        example_reference = InterpretedMarketReferenceLevelDraft(
            label=example_text["reference_label"],
            value=100,
            unit="USD",
            interpretation=example_text["reference_interpretation"],
            evidence_refs=(allowed_evidence_refs[0],),
        )

    example = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.TECHNICAL,
                    label=example_text["scenario_range_label"],
                    low=example_low,
                    high=example_high,
                    unit="USD",
                    interpretation=example_text["scenario_range_interpretation"],
                    limitations=(example_text["valuation_limitation"],),
                ),
            ),
        ),
        valuation_assessment=ValuationAssessmentDraft(
            method=example_text["valuation_method"],
            low=DerivedRangeEndpointDraft(
                calculation_id="calc_valuation_low",
            ),
            high=DerivedRangeEndpointDraft(
                calculation_id="calc_valuation_high",
            ),
            currency="USD",
            limitations=(example_text["valuation_limitation"],),
        ),
        market_reference_levels=(
            example_reference,
        ),
        calculation_records=(
            CalculationRecordDraft(
                id="calc_valuation_low",
                formula="earnings * multiple",
                inputs=(
                    CalculationInputDraft(name="earnings", value=10),
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
                    CalculationInputDraft(name="earnings", value=11),
                    CalculationInputDraft(name="multiple", value=10),
                ),
                input_evidence_refs=(allowed_evidence_refs[0],),
                unit="USD",
                limitations=(example_text["valuation_limitation"],),
            ),
        ),
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
            "levels must use basis=interpreted with supporting Evidence refs. They "
            "require no calculation and must not be disguised as observed values or "
            "descriptive formulas. Derived endpoints must name a valid calculation. "
            "Each base, bull, and bear scenario range field is an array. Preserve "
            "every already-valid, non-duplicate range while repairing only the "
            "invalid range identified by the issue path. A scenario may contain "
            "multiple ranges with the same category when their labels or endpoints "
            "describe distinct research uses. Do not emit exact duplicates. "
            "Every range must belong to the matching validated scenario in the "
            "SCENARIO CATALOG. Labels describe only the range purpose and must not "
            "claim to belong to a different base, bull, or bear scenario. "
            "A valuation assessment is allowed only when both endpoints are derived "
            "from real valuation calculations such as EPS times a multiple or DCF. Do not "
            "supply calculation results or dates; the application derives both "
            "from the formula and Evidence Ledger. Do not change the qualitative "
            f"decision core. {language_rules}\n"
            "VALID OBSERVED VALUE REFS:\n"
            + json.dumps(
                [item.prompt_payload() for item in value_catalog],
                ensure_ascii=False,
            )
            + "\nSCENARIO CATALOG:\n"
            + scenario_catalog_json
        ),
    )
    try:
        output = runner.invoke(
            prompt + "\n\nExtract only optional decision-critical numeric content. "
            "Set requested=false and return empty collections when the brief "
            "does not support a numeric appendix. Do not copy ordinary report "
            "table arithmetic. Use scenario_reference_ranges for technical bands, "
            "52-week levels, or analyst target ranges; these are not valuations. "
            "Use valuation_assessment only for genuinely derived valuation work. "
            + language_rules
            + "\n\nNUMERIC VALUE CATALOG:\n"
            + json.dumps(
                [item.prompt_payload() for item in value_catalog],
                ensure_ascii=False,
            )
            + "\n\nSCENARIO CATALOG:\n"
            + scenario_catalog_json
            + "\n\nLOCALIZED VALID EXAMPLE:\n"
            + json.dumps(example.model_dump(mode="json"), ensure_ascii=False),
            example=example.model_dump(mode="json"),
            allowed_evidence_refs=allowed_evidence_refs,
        )
    except StructuredOutputError as exc:
        draft = _numeric_candidate(exc.candidate)
        if draft is None:
            empty = _empty_numeric_assembly(
                node=node,
                status=NumericAuditStatus.INCOMPLETE,
            )
            return replace(
                empty,
                audit=_numeric_audit_appendix(
                    status=NumericAuditAppendixStatus.INCOMPLETE,
                    failures=exc.failures,
                    omissions=(
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
                ),
            )
        assembly = _assemble_numeric_draft(
            draft,
            bundle=bundle,
            allowed_evidence_refs=allowed,
            value_catalog=value_catalog_by_id,
            salvage=True,
            node=node,
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
        return replace(
            assembly,
            audit=_numeric_audit_appendix(
                status=(
                    NumericAuditAppendixStatus.PARTIAL
                    if assembly.status is NumericAuditStatus.PARTIAL
                    else NumericAuditAppendixStatus.INCOMPLETE
                ),
                failures=exc.failures,
                omissions=assembly.omissions,
            ),
        )
    assembly = _assemble_numeric_draft(
        output.value,
        bundle=bundle,
        allowed_evidence_refs=allowed,
        value_catalog=value_catalog_by_id,
        salvage=False,
        node=node,
    )
    if output.failed_attempts:
        return replace(
            assembly,
            audit=_numeric_audit_appendix(
                status=NumericAuditAppendixStatus.RECOVERED,
                failures=output.failed_attempts,
                omissions=(),
            ),
        )
    return assembly


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
) -> DecisionNumericAuditAppendix:
    snapshots = tuple(_numeric_audit_snapshot(failure) for failure in failures)
    if not snapshots:
        raise ValueError("numeric audit appendix requires a failed attempt")
    return DecisionNumericAuditAppendix(
        status=status,
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


def _empty_numeric_assembly(
    *,
    node: str,
    status: NumericAuditStatus,
) -> _NumericDecisionAssembly:
    return _NumericDecisionAssembly(
        scenario_reference_ranges={},
        valuation_assessment=None,
        market_reference_levels=(),
        calculation_records=(),
        status=status,
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


def _assemble_numeric_draft(
    draft: DecisionNumericDraft,
    *,
    bundle: EvidenceBundle,
    allowed_evidence_refs: set[str],
    value_catalog: Mapping[str, NumericValueCatalogEntry],
    salvage: bool,
    node: str,
    output_language: str = ReportLanguage.ENGLISH.prompt_label,
) -> _NumericDecisionAssembly:
    issues: list[str] = []
    calculations: dict[str, CalculationRecord] = {}
    evidence_items = {item.ref: item for item in bundle.items}
    duplicate_ids = {
        item.id
        for item in draft.calculation_records
        if sum(other.id == item.id for other in draft.calculation_records) > 1
    }
    for item in draft.calculation_records:
        prefix = f"numeric.calculation.{item.id}"
        if item.id in duplicate_ids:
            issues.append(f"{prefix}.duplicate_id")
            continue
        try:
            require_nonempty_texts(item.limitations)
            require_valid_refs(
                item.input_evidence_refs,
                allowed_evidence_refs,
                required=True,
            )
            inputs = item.input_mapping()
            calculated = _evaluate_formula(
                item.formula,
                inputs,
                issue_prefix=prefix,
            )
            resolved_date = _latest_evidence_date(
                item.input_evidence_refs,
                evidence_items=evidence_items,
                bundle=bundle,
                issue_prefix=prefix,
            )
            calculations[item.id] = CalculationRecord(
                id=item.id,
                formula=item.formula,
                inputs=inputs,
                input_evidence_refs=item.input_evidence_refs,
                result=calculated,
                unit=item.unit,
                as_of_date=resolved_date.value,
                temporal_basis=resolved_date.temporal_basis,
                limitations=item.limitations,
            )
        except OutputValidationError as exc:
            issues.append(exc.issue_code)

    scenario_values: dict[
        ResearchScenarioKind, tuple[ScenarioReferenceRange, ...]
    ] = {}
    duplicate_warnings: list[ResearchWarning] = []
    linked_ids: set[str] = set()
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
                issues.append(f"{prefix}.scenario_mismatch")
                continue
            try:
                require_text(scenario.label)
                require_text(scenario.interpretation)
                require_nonempty_texts(scenario.limitations)
            except OutputValidationError as exc:
                issues.append(f"{prefix}.{exc.issue_code}")
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
                    issues.append(exc.issue_code)
            if set(endpoints) != {"low", "high"}:
                continue
            if endpoints["high"].value < endpoints["low"].value:
                issues.append(f"{prefix}.invalid_range")
                continue
            assembled_ranges.append(
                ScenarioReferenceRange(
                    category=scenario.category,
                    label=scenario.label,
                    low=endpoints["low"],
                    high=endpoints["high"],
                    unit=scenario.unit,
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
            issues.append(exc.issue_code)
        else:
            if high.value < low.value:
                issues.append(f"{prefix}.invalid_range")
            else:
                valuation = ValuationAssessment(
                    method=item.method,
                    low=low,
                    high=high,
                    currency=item.currency,
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
                evidence_refs = catalog_entry.evidence_refs
                source_locator: EvidenceValueLocator | None = catalog_entry.locator
                calculation_ids: tuple[str, ...] = ()
            elif isinstance(item, InterpretedMarketReferenceLevelDraft):
                require_valid_refs(
                    item.evidence_refs,
                    allowed_evidence_refs,
                    required=True,
                )
                resolved_date = _latest_evidence_date(
                    item.evidence_refs,
                    evidence_items=evidence_items,
                    bundle=bundle,
                    issue_prefix=prefix,
                )
                as_of_date = resolved_date.value
                temporal_basis = resolved_date.temporal_basis
                value = item.value
                evidence_refs = item.evidence_refs
                source_locator = None
                calculation_ids: tuple[str, ...] = ()
            else:
                calculation = calculations.get(item.calculation_id)
                if calculation is None:
                    raise OutputValidationError(f"{prefix}.unknown_calculation")
                as_of_date = calculation.as_of_date
                value = float(calculation.result)
                evidence_refs = calculation.input_evidence_refs
                source_locator = None
                calculation_ids = (item.calculation_id,)
                temporal_basis = calculation.temporal_basis
        except OutputValidationError as exc:
            issues.append(exc.issue_code)
        else:
            reference_levels.append(
                MarketReferenceLevel(
                    label=item.label,
                    value=value,
                    unit=item.unit,
                    as_of_date=as_of_date,
                    interpretation=item.interpretation,
                    evidence_refs=evidence_refs,
                    basis=item.basis,
                    source_locator=source_locator,
                    calculation_ids=calculation_ids,
                    temporal_basis=temporal_basis,
                )
            )
            linked_ids.update(calculation_ids)

    orphaned = set(calculations).difference(linked_ids)
    for calculation_id in sorted(orphaned):
        issues.append(f"numeric.calculation.{calculation_id}.orphaned")
    if draft.requested and not (scenario_values or valuation is not None or reference_levels):
        issues.append("numeric.requested.empty")
    if not draft.requested and (
        draft.scenario_reference_ranges.has_content()
        or draft.valuation_assessment is not None
        or draft.market_reference_levels
        or draft.calculation_records
    ):
        issues.append("numeric.not_requested.has_content")

    if issues and not salvage:
        raise OutputValidationError(
            issues[0],
            issue_codes=tuple(issues),
        )

    kept_calculations = tuple(
        calculation
        for calculation_id, calculation in calculations.items()
        if calculation_id in linked_ids
    )
    has_content = bool(scenario_values or valuation is not None or reference_levels)
    omissions = _numeric_omissions(draft, tuple(issues))
    if issues:
        status = NumericAuditStatus.PARTIAL if has_content else NumericAuditStatus.INCOMPLETE
        omitted = ", ".join(
            item.reference_label or item.component_path for item in omissions
        )
        warnings = (
            ResearchWarning(
                code=f"decision.numeric_audit_{status.value}",
                message=(
                    "Optional numeric components were omitted because their "
                    "audit failed"
                    + (f": {omitted}." if omitted else ".")
                    + " The qualitative decision remains audited."
                ),
                source=node,
            ),
            *duplicate_warnings,
        )
    else:
        status = NumericAuditStatus.COMPLETE if has_content else NumericAuditStatus.NOT_APPLICABLE
        warnings = tuple(duplicate_warnings)
    return _NumericDecisionAssembly(
        scenario_reference_ranges=scenario_values,
        valuation_assessment=valuation,
        market_reference_levels=tuple(reference_levels),
        calculation_records=kept_calculations,
        status=status,
        warnings=warnings,
        issues=tuple(issues),
        omissions=omissions,
    )


def _numeric_omissions(
    draft: DecisionNumericDraft,
    issues: tuple[str, ...],
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
        elif (
            len(parts) >= 6
            and parts[:2] == ["numeric", "scenario"]
            and parts[3] == "ranges"
        ):
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
        grouped.setdefault(
            (path, component_type, scenario_kind, reference_label), []
        ).append(issue)
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
            source_locator=catalog_entry.locator,
            as_of_date=resolved_date.value,
            temporal_basis=resolved_date.temporal_basis,
        )
    if isinstance(draft, InterpretedRangeEndpointDraft):
        try:
            require_valid_refs(
                draft.evidence_refs,
                allowed_evidence_refs,
                required=True,
            )
        except OutputValidationError as exc:
            suffix = "missing_evidence" if exc.issue_code == "refs.required" else "invalid_evidence"
            raise OutputValidationError(f"{issue_prefix}.{suffix}") from exc
        resolved_date = _latest_evidence_date(
            draft.evidence_refs,
            evidence_items=evidence_items,
            bundle=bundle,
            issue_prefix=issue_prefix,
        )
        return AuditedRangeEndpoint(
            value=draft.value,
            basis=MarketReferenceBasis.INTERPRETED,
            evidence_refs=draft.evidence_refs,
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
        origin.temporal_scope is not EvidenceTemporalScope.LIVE_ONLY
        or not origin.retrieved_at
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
