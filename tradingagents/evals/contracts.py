"""Deterministic research audits and recorded quality-first release gates."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagents.application.contracts import (
    AnalystReport,
    EvidenceBundle,
    EvidenceQuality,
    EvidenceTable,
    MemoryContext,
    ResearchArtifact,
    ResearchDecision,
    ResearchRating,
    ResearchTable,
    ResearchTableCell,
    TableCellKind,
)
from tradingagents.application.table_display import evaluate_formula

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
_EVIDENCE_REF_RE = re.compile(r"ev_[a-f0-9]{12}")
_PROHIBITED_RE = re.compile(
    r"\b(?:"
    r"position\s+siz(?:e|ing)|account\s+allocation|entry\s+price|"
    r"stop[\s-]+loss|take[\s-]+profit|price\s+target|"
    r"buy\s+\d+\s+shares?|sell\s+\d+\s+shares?|"
    r"portfolio\s+weight|broker(?:age)?\s+order"
    r")\b",
    re.IGNORECASE,
)
_UNKNOWN_SOURCES = {"", "unknown", "n/a", "none", "—"}
_FALLBACK_SENTINELS = {
    "n/a",
    "not available",
    "unavailable",
    "unknown",
    "unspecified",
}
_HUMAN_TEXT_FIELDS = {
    "executive_summary",
    "thesis",
    "statement",
    "implication",
    "narrative",
    "title",
    "purpose",
    "question",
    "bull_position",
    "bear_position",
    "response",
    "causal_mechanism",
    "rationale",
    "outcome",
    "method",
    "interpretation",
    "subject",
    "explanation",
    "time_horizon",
    "mechanism",
}
_QUALITY_DIMENSIONS = (
    "factual_completeness",
    "analytical_depth",
    "table_readability",
    "decision_utility",
)

EvalVariant = Literal[
    "main_analyst",
    "v2_analyst",
    "main_medium",
    "v2_standard",
    "v2_deep",
]
EvalProfile = Literal["analyst", "medium", "standard", "deep"]
EvalLayer = Literal["analyst", "graph"]

_VARIANT_CONTRACT: dict[EvalVariant, tuple[EvalLayer, EvalProfile]] = {
    "main_analyst": ("analyst", "analyst"),
    "v2_analyst": ("analyst", "analyst"),
    "main_medium": ("graph", "medium"),
    "v2_standard": ("graph", "standard"),
    "v2_deep": ("graph", "deep"),
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvalIssue(_FrozenModel):
    severity: Literal["warning", "severe"]
    code: str
    location: str
    message: str


class OutputEvaluation(_FrozenModel):
    """Deterministic contract result, not a model-quality score."""

    issues: tuple[EvalIssue, ...] = ()
    contract_score: float = Field(ge=0.0, le=1.0)
    risk_recall: float = Field(ge=0.0, le=1.0)

    @property
    def severe_issues(self) -> tuple[EvalIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "severe")


class QualityScores(_FrozenModel):
    """Blinded review scores used only with recorded model outputs."""

    factual_completeness: float = Field(ge=0.0, le=1.0)
    analytical_depth: float = Field(ge=0.0, le=1.0)
    table_readability: float = Field(ge=0.0, le=1.0)
    decision_utility: float = Field(ge=0.0, le=1.0)


class EvalMeasurement(_FrozenModel):
    """One immutable row from an opt-in, real-model evaluation run."""

    suite_version: Literal["2"] = "2"
    layer: EvalLayer
    variant: EvalVariant
    profile: EvalProfile
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    provider: str = Field(min_length=1)
    quick_model: str = Field(min_length=1)
    deep_model: str = Field(min_length=1)
    quick_reasoning_effort: str | None = None
    deep_reasoning_effort: str | None = None
    output_language: str = Field(min_length=1)
    temperature: float | None = None
    prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_path: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    repetition: int = Field(ge=1, le=3)
    quality: QualityScores
    reviewer: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    llm_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    wall_time_seconds: float = Field(ge=0.0)
    risk_recall: float = Field(ge=0.0, le=1.0)
    severe_issues: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_variant(self) -> EvalMeasurement:
        expected = _VARIANT_CONTRACT[self.variant]
        if (self.layer, self.profile) != expected:
            raise ValueError(f"{self.variant} requires layer/profile {expected[0]}/{expected[1]}")
        return self


class ReleaseGateResult(_FrozenModel):
    passed: bool
    checks: dict[str, bool]
    summary: dict[str, float]


def validate_analyst_output(
    *,
    bundle: EvidenceBundle,
    reports: Iterable[AnalystReport],
    artifacts: Iterable[ResearchArtifact] = (),
    table_expected: bool = False,
) -> OutputEvaluation:
    """Audit Analyst reports without constructing a synthetic decision."""

    issues, evidence, valid_refs = _audit_bundle_reports(
        bundle=bundle,
        reports=tuple(reports),
        table_expected=table_expected,
    )
    for index, artifact in enumerate(artifacts):
        _check_artifact(
            artifact,
            evidence=evidence,
            valid_refs=valid_refs,
            memory=None,
            location=f"artifacts[{index}]",
            issues=issues,
        )
    return _output_evaluation(issues, risk_recall=1.0)


def validate_research_output(
    *,
    bundle: EvidenceBundle,
    reports: Iterable[AnalystReport],
    decision: ResearchDecision,
    artifacts: Iterable[ResearchArtifact] = (),
    memory: MemoryContext | None = None,
    expected_rating: ResearchRating | None = None,
    expected_risk_terms: Iterable[str] = (),
    table_expected: bool = False,
) -> OutputEvaluation:
    """Audit one result without invoking a model or external data source."""

    reports = tuple(reports)
    issues, evidence, valid_refs = _audit_bundle_reports(
        bundle=bundle,
        reports=reports,
        table_expected=table_expected,
    )

    _check_decision(
        decision,
        evidence=evidence,
        valid_refs=valid_refs,
        memory=memory,
        bundle=bundle,
        issues=issues,
    )

    for index, artifact in enumerate(artifacts):
        _check_artifact(
            artifact,
            evidence=evidence,
            valid_refs=valid_refs,
            memory=memory,
            location=f"artifacts[{index}]",
            issues=issues,
        )

    if expected_rating is not None and decision.rating is not expected_rating:
        issues.append(
            EvalIssue(
                severity="severe",
                code="decision.rating_inconsistent",
                location="decision.rating",
                message=(f"Expected {expected_rating.value}, got {decision.rating.value}."),
            )
        )

    expected_terms = tuple(term.strip().casefold() for term in expected_risk_terms if term.strip())
    risk_text = "\n".join(decision.risks).casefold()
    risk_recall = (
        sum(term in risk_text for term in expected_terms) / len(expected_terms)
        if expected_terms
        else 1.0
    )
    return _output_evaluation(issues, risk_recall=risk_recall)


def evaluate_release_gates(
    *,
    baseline_analyst: Iterable[EvalMeasurement],
    current_analyst: Iterable[EvalMeasurement],
    baseline_medium: Iterable[EvalMeasurement],
    current_standard: Iterable[EvalMeasurement],
    current_deep: Iterable[EvalMeasurement],
) -> ReleaseGateResult:
    """Evaluate real recorded outputs without token, call, or latency gates."""

    groups: dict[str, tuple[EvalMeasurement, ...]] = {
        "baseline_analyst": tuple(baseline_analyst),
        "current_analyst": tuple(current_analyst),
        "baseline_medium": tuple(baseline_medium),
        "current_standard": tuple(current_standard),
        "current_deep": tuple(current_deep),
    }
    expected_variants: dict[str, EvalVariant] = {
        "baseline_analyst": "main_analyst",
        "current_analyst": "v2_analyst",
        "baseline_medium": "main_medium",
        "current_standard": "v2_standard",
        "current_deep": "v2_deep",
    }
    for label, measurements in groups.items():
        _validate_measurement_matrix(
            label,
            measurements,
            expected_variant=expected_variants[label],
        )

    analyst_case_sets = {
        frozenset(item.case_id for item in groups[label])
        for label in ("baseline_analyst", "current_analyst")
    }
    if len(analyst_case_sets) != 1:
        raise ValueError("main and V2 Analyst groups must cover the same cases")
    graph_case_sets = {
        frozenset(item.case_id for item in groups[label])
        for label in ("baseline_medium", "current_standard", "current_deep")
    }
    if len(graph_case_sets) != 1:
        raise ValueError("main Medium, V2 Standard, and V2 Deep must cover the same cases")
    all_measurements = tuple(item for measurements in groups.values() for item in measurements)
    _validate_recorded_identity(all_measurements)
    _validate_case_evidence_hashes(all_measurements)
    _validate_commit_identity(groups)

    summary: dict[str, float] = {}
    for label, measurements in groups.items():
        for dimension in _QUALITY_DIMENSIONS:
            summary[f"{label}_{dimension}"] = median(
                getattr(item.quality, dimension) for item in measurements
            )
        summary[f"{label}_llm_calls"] = median(item.llm_calls for item in measurements)
        summary[f"{label}_input_tokens"] = median(item.input_tokens for item in measurements)
        summary[f"{label}_output_tokens"] = median(item.output_tokens for item in measurements)
        summary[f"{label}_wall_time_seconds"] = median(
            item.wall_time_seconds for item in measurements
        )
        summary[f"{label}_risk_recall"] = median(item.risk_recall for item in measurements)

    checks = {
        "zero_severe_regressions": all(
            item.severe_issues == 0
            for label in ("current_analyst", "current_standard", "current_deep")
            for item in groups[label]
        )
    }
    for dimension in _QUALITY_DIMENSIONS:
        checks[f"analyst_{dimension}_not_lower"] = (
            summary[f"current_analyst_{dimension}"] >= summary[f"baseline_analyst_{dimension}"]
        )
        checks[f"standard_{dimension}_not_lower"] = (
            summary[f"current_standard_{dimension}"] >= summary[f"baseline_medium_{dimension}"]
        )
        checks[f"deep_{dimension}_not_lower"] = (
            summary[f"current_deep_{dimension}"] >= summary[f"current_standard_{dimension}"]
        )
    checks["deep_risk_recall_plus_10pp"] = (
        summary["current_deep_risk_recall"] + 1e-12
        >= summary["current_standard_risk_recall"] + 0.10
    )
    return ReleaseGateResult(
        passed=all(checks.values()),
        checks=checks,
        summary=summary,
    )


def _audit_bundle_reports(
    *,
    bundle: EvidenceBundle,
    reports: tuple[AnalystReport, ...],
    table_expected: bool,
) -> tuple[list[EvalIssue], dict[str, Any], set[str]]:
    issues: list[EvalIssue] = []
    evidence = {item.ref: item for item in bundle.items}
    valid_refs = set(evidence)

    for item in bundle.items:
        if (
            item.quality is not EvidenceQuality.UNAVAILABLE
            and item.source.strip().casefold() in _UNKNOWN_SOURCES
        ):
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="source.missing",
                    location=f"evidence.{item.ref}",
                    message="Usable evidence must identify its actual source.",
                )
            )
        if item.fallback and not item.provenance:
            issues.append(
                EvalIssue(
                    severity="warning",
                    code="fallback.provenance_missing",
                    location=f"evidence.{item.ref}",
                    message="Fallback evidence should explain selection and timing.",
                )
            )

    evidence_tables = {table.id: table for table in bundle.tables}
    for table in bundle.tables:
        _check_table(
            table,
            evidence=evidence,
            valid_refs=valid_refs,
            valid_evidence_tables=evidence_tables,
            location=f"evidence.tables.{table.id}",
            issues=issues,
        )

    presented_table_ids: set[str] = set()
    for report in reports:
        location = f"report.{report.analyst}"
        _check_refs(report.evidence_refs, valid_refs, location, issues)
        _check_text(
            report.executive_summary,
            report.evidence_refs,
            evidence,
            f"{location}.executive_summary",
            issues,
        )
        report_table_ids = {table.id for table in report.tables}
        for index, section in enumerate(report.sections):
            section_location = f"{location}.sections[{index}]"
            _check_text(
                section.narrative,
                report.evidence_refs,
                evidence,
                section_location,
                issues,
            )
            for table_id in section.research_table_ids:
                if table_id not in report_table_ids:
                    issues.append(
                        EvalIssue(
                            severity="severe",
                            code="table_ref.unresolved",
                            location=section_location,
                            message=f"Table {table_id} is not present in the result.",
                        )
                    )
                else:
                    presented_table_ids.add(table_id)
            for table_id in section.evidence_table_ids:
                if table_id not in evidence_tables:
                    issues.append(
                        EvalIssue(
                            severity="severe",
                            code="source_table_ref.unresolved",
                            location=section_location,
                            message=(
                                f"Source table {table_id} is not present in the sealed evidence."
                            ),
                        )
                    )
        for index, claim in enumerate(report.claims):
            claim_location = f"{location}.claims[{index}]"
            _check_refs(claim.evidence_refs, valid_refs, claim_location, issues)
            if not set(claim.evidence_refs).issubset(report.evidence_refs):
                issues.append(
                    EvalIssue(
                        severity="severe",
                        code="report.refs_incomplete",
                        location=claim_location,
                        message="Top-level report refs omit claim evidence.",
                    )
                )
            _check_text(
                claim.statement,
                claim.evidence_refs,
                evidence,
                claim_location,
                issues,
            )
            _check_text(
                claim.implication,
                claim.evidence_refs,
                evidence,
                f"{claim_location}.implication",
                issues,
            )
        for table in report.tables:
            _check_table(
                table,
                evidence=evidence,
                valid_refs=valid_refs,
                valid_evidence_tables=evidence_tables,
                location=f"{location}.tables.{table.id}",
                issues=issues,
            )
        for field, values in (
            ("catalysts", report.catalysts),
            ("risks", report.risks),
            ("invalidation_conditions", report.invalidation_conditions),
        ):
            for index, text in enumerate(values):
                _check_text(
                    text,
                    report.evidence_refs,
                    evidence,
                    f"{location}.{field}[{index}]",
                    issues,
                )
        for warning in report.warnings:
            if warning.evidence_ref:
                _check_refs(
                    (warning.evidence_ref,),
                    valid_refs,
                    f"{location}.warnings",
                    issues,
                )

    if table_expected and not presented_table_ids:
        issues.append(
            EvalIssue(
                severity="severe",
                code="table.required",
                location="reports",
                message=(
                    "Suitable tabular evidence exists but no table was placed in a report section."
                ),
            )
        )
    return issues, evidence, valid_refs


def _output_evaluation(
    issues: Iterable[EvalIssue],
    *,
    risk_recall: float,
) -> OutputEvaluation:
    deduped = _dedupe_issues(issues)
    severe_count = sum(issue.severity == "severe" for issue in deduped)
    warning_count = len(deduped) - severe_count
    contract_score = max(
        0.0,
        1.0 - severe_count * 0.25 - warning_count * 0.03,
    )
    return OutputEvaluation(
        issues=tuple(deduped),
        contract_score=contract_score,
        risk_recall=risk_recall,
    )


def _check_decision(
    decision: ResearchDecision,
    *,
    evidence: Mapping[str, Any],
    valid_refs: set[str],
    memory: MemoryContext | None,
    bundle: EvidenceBundle,
    issues: list[EvalIssue],
) -> None:
    _check_refs(decision.evidence_refs, valid_refs, "decision", issues)
    valid_memory_refs = set(memory.refs if memory is not None else ())
    if memory is not None and memory.instrument.casefold() != bundle.instrument.casefold():
        issues.append(
            EvalIssue(
                severity="severe",
                code="memory.instrument_mismatch",
                location="memory.instrument",
                message=(f"Memory for {memory.instrument} cannot calibrate {bundle.instrument}."),
            )
        )
    for ref in decision.memory_refs:
        if ref not in valid_memory_refs:
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="memory_ref.unresolved",
                    location="decision.memory_refs",
                    message=f"Memory ref {ref} was not supplied to this run.",
                )
            )
    for ref in decision.evidence_refs:
        if ref.startswith("memory:"):
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="memory_ref.used_as_evidence",
                    location="decision.evidence_refs",
                    message=f"Memory ref {ref} cannot be used as current evidence.",
                )
            )

    top_level_texts = (
        ("executive_summary", decision.executive_summary),
        ("thesis", decision.thesis),
        *((f"catalysts[{i}]", value) for i, value in enumerate(decision.catalysts)),
        *((f"risks[{i}]", value) for i, value in enumerate(decision.risks)),
        *(
            (f"invalidation_conditions[{i}]", value)
            for i, value in enumerate(decision.invalidation_conditions)
        ),
        *(
            (f"unresolved_questions[{i}]", value)
            for i, value in enumerate(decision.unresolved_questions)
        ),
        ("time_horizon", decision.time_horizon),
    )
    for field, text in top_level_texts:
        if field == "time_horizon":
            _check_text_health(
                text,
                f"decision.{field}",
                issues,
                research_boundary=True,
            )
        else:
            _check_text(
                text,
                decision.evidence_refs,
                evidence,
                f"decision.{field}",
                issues,
                research_boundary=True,
            )

    for scenario in decision.scenarios:
        location = f"decision.scenarios.{scenario.kind.value}"
        _check_refs(scenario.evidence_refs, valid_refs, location, issues)
        for index, assumption in enumerate(scenario.core_assumptions):
            _check_text(
                assumption,
                scenario.evidence_refs,
                evidence,
                f"{location}.core_assumptions[{index}]",
                issues,
                research_boundary=True,
            )
        _check_text(
            scenario.outcome,
            scenario.evidence_refs,
            evidence,
            f"{location}.outcome",
            issues,
            research_boundary=True,
        )

    valuation = decision.valuation_assessment
    if valuation is not None:
        location = "decision.valuation_assessment"
        _check_refs(
            valuation.input_evidence_refs,
            valid_refs,
            location,
            issues,
        )
        if valuation.as_of_date > bundle.analysis_date:
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="valuation.future_date",
                    location=f"{location}.as_of_date",
                    message="Valuation assessment is after the analysis cutoff.",
                )
            )
        _check_text(
            valuation.method,
            valuation.input_evidence_refs,
            evidence,
            f"{location}.method",
            issues,
        )
        for index, limitation in enumerate(valuation.limitations):
            _check_text(
                limitation,
                valuation.input_evidence_refs,
                evidence,
                f"{location}.limitations[{index}]",
                issues,
            )

    for index, level in enumerate(decision.market_reference_levels):
        location = f"decision.market_reference_levels[{index}]"
        _check_refs(level.evidence_refs, valid_refs, location, issues)
        if level.as_of_date > bundle.analysis_date:
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="market_reference.future_date",
                    location=f"{location}.as_of_date",
                    message="Market reference level is after the analysis cutoff.",
                )
            )
        _check_numeric_value(
            level.value,
            level.evidence_refs,
            evidence,
            f"{location}.value",
            issues,
        )
        _check_text(
            level.interpretation,
            level.evidence_refs,
            evidence,
            f"{location}.interpretation",
            issues,
            research_boundary=True,
        )

    for index, adjustment in enumerate(decision.risk_review_adjustments):
        location = f"decision.risk_review_adjustments[{index}]"
        _check_refs(adjustment.evidence_refs, valid_refs, location, issues)
        _check_text(
            adjustment.subject,
            adjustment.evidence_refs or decision.evidence_refs,
            evidence,
            f"{location}.subject",
            issues,
        )
        _check_text(
            adjustment.explanation,
            adjustment.evidence_refs or decision.evidence_refs,
            evidence,
            f"{location}.explanation",
            issues,
        )


def _check_artifact(
    artifact: ResearchArtifact,
    *,
    evidence: Mapping[str, Any],
    valid_refs: set[str],
    memory: MemoryContext | None,
    location: str,
    issues: list[EvalIssue],
) -> None:
    if not artifact.prompt_version.strip():
        issues.append(
            EvalIssue(
                severity="severe",
                code="artifact.prompt_version_missing",
                location=f"{location}.prompt_version",
                message="Every artifact must identify its prompt version.",
            )
        )
    _audit_payload(
        artifact.content,
        evidence=evidence,
        valid_refs=valid_refs,
        valid_memory_refs=set(memory.refs if memory is not None else ()),
        location=f"{location}.content",
        inherited_refs=(),
        issues=issues,
    )


def _audit_payload(
    value: Any,
    *,
    evidence: Mapping[str, Any],
    valid_refs: set[str],
    valid_memory_refs: set[str],
    location: str,
    inherited_refs: tuple[str, ...],
    issues: list[EvalIssue],
) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        raw_refs = value.get("evidence_refs") or value.get("input_evidence_refs")
        refs = (
            tuple(str(ref) for ref in raw_refs)
            if isinstance(raw_refs, (list, tuple))
            else inherited_refs
        )
        if raw_refs is not None:
            _check_refs(refs, valid_refs, location, issues)
        raw_memory_refs = value.get("memory_refs")
        if isinstance(raw_memory_refs, (list, tuple)):
            for ref in raw_memory_refs:
                if ref not in valid_memory_refs:
                    issues.append(
                        EvalIssue(
                            severity="severe",
                            code="memory_ref.unresolved",
                            location=f"{location}.memory_refs",
                            message=f"Memory ref {ref} was not supplied.",
                        )
                    )
        for key, item in value.items():
            item_location = f"{location}.{key}"
            if key in _HUMAN_TEXT_FIELDS and isinstance(item, str):
                if key in {"title", "purpose", "subject", "time_horizon"}:
                    _check_text_health(
                        item,
                        item_location,
                        issues,
                        research_boundary=True,
                    )
                else:
                    _check_text(
                        item,
                        refs,
                        evidence,
                        item_location,
                        issues,
                        research_boundary=True,
                    )
            else:
                _audit_payload(
                    item,
                    evidence=evidence,
                    valid_refs=valid_refs,
                    valid_memory_refs=valid_memory_refs,
                    location=item_location,
                    inherited_refs=refs,
                    issues=issues,
                )
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            if isinstance(item, str):
                _check_text_health(
                    item,
                    f"{location}[{index}]",
                    issues,
                    research_boundary=True,
                )
            else:
                _audit_payload(
                    item,
                    evidence=evidence,
                    valid_refs=valid_refs,
                    valid_memory_refs=valid_memory_refs,
                    location=f"{location}[{index}]",
                    inherited_refs=inherited_refs,
                    issues=issues,
                )


def _check_table(
    table: EvidenceTable | ResearchTable,
    *,
    evidence: Mapping[str, Any],
    valid_refs: set[str],
    valid_evidence_tables: Mapping[str, EvidenceTable],
    location: str,
    issues: list[EvalIssue],
) -> None:
    if isinstance(table, ResearchTable) and table.source_evidence_table_id:
        source = valid_evidence_tables.get(table.source_evidence_table_id)
        if source is None:
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="table.source_unresolved",
                    location=location,
                    message=(
                        "Source evidence table "
                        f"{table.source_evidence_table_id} is missing."
                    ),
                )
            )
        else:
            valid_rows = {row.id for row in source.rows}
            for row_id in table.source_evidence_row_ids:
                if row_id not in valid_rows:
                    issues.append(
                        EvalIssue(
                            severity="severe",
                            code="table.source_row_unresolved",
                            location=location,
                            message=f"Source row {row_id} is missing.",
                        )
                    )
    _check_refs(table.evidence_refs, valid_refs, location, issues)
    for row in table.rows:
        _check_refs(
            row.evidence_refs,
            valid_refs,
            f"{location}.rows.{row.id}",
            issues,
        )
        inherited_refs = row.evidence_refs or table.evidence_refs
        for key, cell in row.cells.items():
            _check_table_cell(
                cell,
                evidence=evidence,
                valid_refs=valid_refs,
                location=f"{location}.rows.{row.id}.{key}",
                inherited_refs=inherited_refs,
                issues=issues,
            )


def _check_table_cell(
    cell: ResearchTableCell,
    *,
    evidence: Mapping[str, Any],
    valid_refs: set[str],
    location: str,
    inherited_refs: tuple[str, ...],
    issues: list[EvalIssue],
) -> None:
    _check_refs(cell.evidence_refs, valid_refs, location, issues)
    effective_refs = cell.evidence_refs or inherited_refs
    _check_text_health(cell.display_value, f"{location}.display_value", issues)
    if cell.kind is TableCellKind.DESCRIPTOR:
        return
    if cell.kind is TableCellKind.DERIVED:
        if cell.derived is None:
            return
        _check_refs(
            cell.derived.input_evidence_refs,
            valid_refs,
            f"{location}.derived",
            issues,
        )
        for name, value in cell.derived.inputs.items():
            _check_numeric_value(
                value,
                cell.derived.input_evidence_refs,
                evidence,
                f"{location}.derived.inputs.{name}",
                issues,
                code="derived.input_untraceable",
            )
        try:
            calculated = evaluate_formula(
                cell.derived.formula,
                cell.derived.inputs,
            )
        except (ValueError, ZeroDivisionError, OverflowError):
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="derived.formula_invalid",
                    location=f"{location}.derived.formula",
                    message="Derived formula cannot be safely evaluated.",
                )
            )
        else:
            if not math.isclose(
                calculated,
                float(cell.derived.result),
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                issues.append(
                    EvalIssue(
                        severity="severe",
                        code="derived.result_mismatch",
                        location=f"{location}.derived.result",
                        message=("Saved derived result does not match formula and inputs."),
                    )
                )
        return
    _check_exact_figures(
        cell.display_value,
        effective_refs,
        evidence,
        f"{location}.display_value",
        issues,
    )
    if isinstance(cell.raw_value, (int, float)) and not isinstance(
        cell.raw_value,
        bool,
    ):
        _check_numeric_value(
            cell.raw_value,
            effective_refs,
            evidence,
            f"{location}.raw_value",
            issues,
        )


def _check_text(
    text: str,
    refs: Iterable[str],
    evidence: Mapping[str, Any],
    location: str,
    issues: list[EvalIssue],
    *,
    research_boundary: bool = False,
) -> None:
    _check_text_health(
        text,
        location,
        issues,
        research_boundary=research_boundary,
    )
    _check_exact_figures(text, refs, evidence, location, issues)


def _check_text_health(
    text: str,
    location: str,
    issues: list[EvalIssue],
    *,
    research_boundary: bool = False,
) -> None:
    normalized = text.strip()
    if normalized.casefold() in _FALLBACK_SENTINELS:
        issues.append(
            EvalIssue(
                severity="severe",
                code="output.fallback_sentinel",
                location=location,
                message="Typed output contains a fallback sentinel.",
            )
        )
    if normalized.startswith(("{", "[")):
        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="output.nested_json",
                    location=location,
                    message="Human-readable text contains a nested JSON object.",
                )
            )
    if research_boundary and _PROHIBITED_RE.search(text):
        issues.append(
            EvalIssue(
                severity="severe",
                code="decision.account_instruction",
                location=location,
                message="Research output contains account-level instructions.",
            )
        )


def _check_refs(
    refs: Iterable[str],
    valid_refs: set[str],
    location: str,
    issues: list[EvalIssue],
) -> None:
    for ref in refs:
        if ref not in valid_refs:
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="evidence_ref.unresolved",
                    location=location,
                    message=f"Evidence ref {ref} is not in the sealed bundle.",
                )
            )


def _check_exact_figures(
    text: str,
    refs: Iterable[str],
    evidence: Mapping[str, Any],
    location: str,
    issues: list[EvalIssue],
) -> None:
    clean_text = _EVIDENCE_REF_RE.sub("", text)
    numbers = tuple(dict.fromkeys(_NUMBER_RE.findall(clean_text)))
    if not numbers:
        return
    payload = "\n".join(_evidence_payload(evidence[ref]) for ref in refs if ref in evidence)
    payload_numbers = _numeric_values(payload)
    for number in numbers:
        if number in payload:
            continue
        value = _parse_number(number)
        if value is not None and any(
            math.isclose(value, candidate, rel_tol=1e-9, abs_tol=1e-9)
            for candidate in payload_numbers
        ):
            continue
        issues.append(
            EvalIssue(
                severity="severe",
                code="figure.untraceable",
                location=location,
                message=f"Exact figure {number} is not present in referenced evidence.",
            )
        )


def _check_numeric_value(
    value: int | float,
    refs: Iterable[str],
    evidence: Mapping[str, Any],
    location: str,
    issues: list[EvalIssue],
    *,
    code: str = "figure.untraceable",
) -> None:
    payload = "\n".join(_evidence_payload(evidence[ref]) for ref in refs if ref in evidence)
    candidates = _numeric_values(payload)
    target = float(value)
    if any(math.isclose(target, candidate, rel_tol=1e-9, abs_tol=1e-9) for candidate in candidates):
        return
    issues.append(
        EvalIssue(
            severity="severe",
            code=code,
            location=location,
            message=f"Numeric value {value} is not present in referenced evidence.",
        )
    )


def _evidence_payload(item: Any) -> str:
    source = getattr(item, "source", None) or ""
    evidence_type = getattr(item, "evidence_type", None) or ""
    requested_date = getattr(item, "requested_date", None) or ""
    effective_date = getattr(item, "effective_date", None) or ""
    available_at = getattr(item, "available_at", None) or ""
    content = getattr(item, "content", None) or ""
    value = getattr(item, "value", None)
    unit = getattr(item, "unit", None) or ""
    provenance = getattr(item, "provenance", None) or {}
    origins = getattr(item, "origins", None) or ()
    return "\n".join(
        (
            str(source),
            str(evidence_type),
            str(requested_date),
            str(effective_date),
            str(available_at),
            str(content),
            str(value if value is not None else ""),
            str(unit),
            json.dumps(provenance, ensure_ascii=False, sort_keys=True),
            json.dumps(
                [
                    origin.model_dump(mode="json") if isinstance(origin, BaseModel) else origin
                    for origin in origins
                ],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        )
    )


def _numeric_values(text: str) -> tuple[float, ...]:
    values = []
    for token in _NUMBER_RE.findall(text):
        value = _parse_number(token)
        if value is not None:
            values.append(value)
    return tuple(values)


def _parse_number(token: str) -> float | None:
    normalized = token.replace(",", "").removesuffix("%")
    try:
        return float(normalized)
    except ValueError:
        return None


def _validate_measurement_matrix(
    label: str,
    measurements: tuple[EvalMeasurement, ...],
    *,
    expected_variant: EvalVariant,
) -> None:
    if not measurements:
        raise ValueError(f"{label} must not be empty")
    repetitions: dict[str, set[int]] = defaultdict(set)
    seen: set[tuple[str, int]] = set()
    prompt_hashes: dict[str, set[str]] = defaultdict(set)
    for item in measurements:
        if item.variant != expected_variant:
            raise ValueError(f"{label} requires variant {expected_variant}, got {item.variant}")
        key = (item.case_id, item.repetition)
        if key in seen:
            raise ValueError(
                f"{label} contains duplicate measurement {item.case_id}/{item.repetition}"
            )
        seen.add(key)
        repetitions[item.case_id].add(item.repetition)
        prompt_hashes[item.case_id].add(item.prompt_hash)
    incomplete = [case_id for case_id, values in repetitions.items() if values != {1, 2, 3}]
    if incomplete:
        raise ValueError(f"{label} requires repetitions 1, 2, 3 for: {', '.join(incomplete)}")
    unstable_prompts = [case_id for case_id, values in prompt_hashes.items() if len(values) != 1]
    if unstable_prompts:
        raise ValueError(
            f"{label} prompt hash changed across repetitions for: {', '.join(unstable_prompts)}"
        )


def _validate_recorded_identity(
    measurements: tuple[EvalMeasurement, ...],
) -> None:
    fields = (
        "provider",
        "quick_model",
        "deep_model",
        "quick_reasoning_effort",
        "deep_reasoning_effort",
        "output_language",
        "temperature",
        "reviewer",
        "rubric_version",
    )
    for field in fields:
        if len({getattr(item, field) for item in measurements}) != 1:
            raise ValueError(f"all release-gate measurements must use the same {field}")


def _validate_case_evidence_hashes(
    measurements: tuple[EvalMeasurement, ...],
) -> None:
    hashes: dict[str, set[str]] = defaultdict(set)
    for item in measurements:
        hashes[item.case_id].add(item.evidence_hash)
    mismatched = [case_id for case_id, values in hashes.items() if len(values) != 1]
    if mismatched:
        raise ValueError(
            "all variants must use the same frozen evidence for: " + ", ".join(mismatched)
        )


def _validate_commit_identity(
    groups: Mapping[str, tuple[EvalMeasurement, ...]],
) -> None:
    main_commits = {
        item.commit_sha
        for label in ("baseline_analyst", "baseline_medium")
        for item in groups[label]
    }
    current_commits = {
        item.commit_sha
        for label in ("current_analyst", "current_standard", "current_deep")
        for item in groups[label]
    }
    if len(main_commits) != 1:
        raise ValueError("main Analyst and Medium measurements must share one commit")
    if len(current_commits) != 1:
        raise ValueError("all V2 measurements must share one commit")


def _dedupe_issues(issues: Iterable[EvalIssue]) -> list[EvalIssue]:
    seen: set[tuple[str, str, str, str]] = set()
    result = []
    for issue in issues:
        key = (issue.severity, issue.code, issue.location, issue.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result
