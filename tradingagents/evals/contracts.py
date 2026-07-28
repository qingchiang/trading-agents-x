"""Auditable graph-output checks and aggregate release thresholds."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.application.contracts import (
    AnalystReport,
    EvidenceBundle,
    EvidenceQuality,
    MemoryContext,
    ResearchDecision,
    ResearchRating,
    RunProfile,
)

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
)
_EVIDENCE_REF_RE = re.compile(r"ev_[a-f0-9]{12}")
_PROHIBITED_RE = re.compile(
    r"\b(?:"
    r"position\s+siz(?:e|ing)|account\s+allocation|entry\s+price|"
    r"stop[\s-]+loss|price\s+target|buy\s+\d+\s+shares?|"
    r"sell\s+\d+\s+shares?|portfolio\s+weight"
    r")\b",
    re.IGNORECASE,
)
_UNKNOWN_SOURCES = {"", "unknown", "n/a", "none", "—"}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvalIssue(_FrozenModel):
    severity: Literal["warning", "severe"]
    code: str
    location: str
    message: str


class OutputEvaluation(_FrozenModel):
    issues: tuple[EvalIssue, ...] = ()
    quality_score: float = Field(ge=0.0, le=1.0)
    risk_recall: float = Field(ge=0.0, le=1.0)

    @property
    def severe_issues(self) -> tuple[EvalIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "severe")


class EvalMeasurement(_FrozenModel):
    suite_version: Literal["1"] = "1"
    model: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    profile: RunProfile
    repetition: int = Field(ge=1, le=3)
    quality_score: float = Field(ge=0.0, le=1.0)
    input_tokens: int = Field(ge=0)
    wall_time_seconds: float = Field(ge=0.0)
    risk_recall: float = Field(ge=0.0, le=1.0)
    severe_issues: int = Field(ge=0)


class ReleaseGateResult(_FrozenModel):
    passed: bool
    checks: dict[str, bool]
    summary: dict[str, float]


def validate_research_output(
    *,
    bundle: EvidenceBundle,
    reports: Iterable[AnalystReport],
    decision: ResearchDecision,
    memory: MemoryContext | None = None,
    expected_rating: ResearchRating | None = None,
    expected_risk_terms: Iterable[str] = (),
) -> OutputEvaluation:
    """Validate one frozen graph result without invoking a model or data source."""
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

    for report in reports:
        location = f"report.{report.analyst}"
        _check_refs(
            report.evidence_refs,
            valid_refs,
            location,
            issues,
        )
        _check_exact_figures(
            report.summary,
            report.evidence_refs,
            evidence,
            f"{location}.summary",
            issues,
        )
        _check_exact_figures(
            report.narrative,
            report.evidence_refs,
            evidence,
            f"{location}.narrative",
            issues,
        )
        for index, claim in enumerate(report.claims):
            claim_location = f"{location}.claims[{index}]"
            _check_refs(
                claim.evidence_refs,
                valid_refs,
                claim_location,
                issues,
            )
            _check_exact_figures(
                claim.text,
                claim.evidence_refs,
                evidence,
                claim_location,
                issues,
            )

    _check_refs(decision.evidence_refs, valid_refs, "decision", issues)
    valid_memory_refs = set(memory.refs if memory is not None else ())
    if (
        memory is not None
        and memory.instrument.casefold() != bundle.instrument.casefold()
    ):
        issues.append(
            EvalIssue(
                severity="severe",
                code="memory.instrument_mismatch",
                location="memory.instrument",
                message=(
                    f"Memory for {memory.instrument} cannot calibrate "
                    f"{bundle.instrument}."
                ),
            )
        )
    for ref in decision.memory_refs:
        if ref not in valid_memory_refs:
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="memory_ref.unresolved",
                    location="decision.memory_refs",
                    message=(
                        f"Memory ref {ref} was not supplied to this run."
                    ),
                )
            )
    for ref in decision.evidence_refs:
        if ref.startswith("memory:"):
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="memory_ref.used_as_evidence",
                    location="decision.evidence_refs",
                    message=(
                        f"Memory ref {ref} cannot be used as current evidence."
                    ),
                )
            )
    decision_texts = (
        ("thesis", decision.thesis),
        *((f"catalysts[{i}]", value) for i, value in enumerate(decision.catalysts)),
        *((f"risks[{i}]", value) for i, value in enumerate(decision.risks)),
        *(
            (f"invalidation_conditions[{i}]", value)
            for i, value in enumerate(decision.invalidation_conditions)
        ),
    )
    for field, text in decision_texts:
        location = f"decision.{field}"
        if _PROHIBITED_RE.search(text):
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="decision.account_instruction",
                    location=location,
                    message="Research decisions cannot contain account-level instructions.",
                )
            )
        _check_exact_figures(
            text,
            decision.evidence_refs,
            evidence,
            location,
            issues,
        )

    if expected_rating is not None and decision.rating is not expected_rating:
        issues.append(
            EvalIssue(
                severity="severe",
                code="decision.rating_inconsistent",
                location="decision.rating",
                message=(
                    f"Expected {expected_rating.value}, got {decision.rating.value}."
                ),
            )
        )

    expected_terms = tuple(
        term.strip().casefold() for term in expected_risk_terms if term.strip()
    )
    risk_text = "\n".join(decision.risks).casefold()
    risk_recall = (
        sum(term in risk_text for term in expected_terms) / len(expected_terms)
        if expected_terms
        else 1.0
    )
    severe_count = sum(issue.severity == "severe" for issue in issues)
    warning_count = len(issues) - severe_count
    quality_score = max(0.0, 1.0 - severe_count * 0.25 - warning_count * 0.03)
    return OutputEvaluation(
        issues=tuple(issues),
        quality_score=quality_score,
        risk_recall=risk_recall,
    )


def evaluate_release_gates(
    *,
    baseline_standard: Iterable[EvalMeasurement],
    current_standard: Iterable[EvalMeasurement],
    current_deep: Iterable[EvalMeasurement],
) -> ReleaseGateResult:
    """Evaluate recorded measurements; this function never fabricates timings."""
    baseline = tuple(baseline_standard)
    standard = tuple(current_standard)
    deep = tuple(current_deep)
    for label, measurements, expected_profile in (
        ("baseline_standard", baseline, RunProfile.STANDARD),
        ("current_standard", standard, RunProfile.STANDARD),
        ("current_deep", deep, RunProfile.DEEP),
    ):
        _validate_measurement_matrix(
            label,
            measurements,
            expected_profile=expected_profile,
        )
    baseline_cases = {item.case_id for item in baseline}
    if (
        {item.case_id for item in standard} != baseline_cases
        or {item.case_id for item in deep} != baseline_cases
    ):
        raise ValueError("baseline, Standard, and Deep must cover the same cases")
    models = {item.model for item in baseline + standard + deep}
    if len(models) != 1:
        raise ValueError("all release-gate measurements must use the same model")

    summary = {
        "baseline_quality": median(item.quality_score for item in baseline),
        "standard_quality": median(item.quality_score for item in standard),
        "baseline_input_tokens": median(item.input_tokens for item in baseline),
        "standard_input_tokens": median(item.input_tokens for item in standard),
        "baseline_wall_time_seconds": median(
            item.wall_time_seconds for item in baseline
        ),
        "standard_wall_time_seconds": median(
            item.wall_time_seconds for item in standard
        ),
        "standard_risk_recall": median(item.risk_recall for item in standard),
        "deep_risk_recall": median(item.risk_recall for item in deep),
    }
    checks = {
        "zero_severe_regressions": all(
            item.severe_issues == 0 for item in standard + deep
        ),
        "standard_quality_not_lower": (
            summary["standard_quality"] >= summary["baseline_quality"]
        ),
        "standard_input_tokens_reduced_30pct": (
            summary["standard_input_tokens"]
            <= summary["baseline_input_tokens"] * 0.70
        ),
        "standard_wall_time_reduced_25pct": (
            summary["standard_wall_time_seconds"]
            <= summary["baseline_wall_time_seconds"] * 0.75
        ),
        "deep_risk_recall_plus_10pp": (
            summary["deep_risk_recall"]
            >= summary["standard_risk_recall"] + 0.10
        ),
    }
    return ReleaseGateResult(
        passed=all(checks.values()),
        checks=checks,
        summary=summary,
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
    evidence: dict[str, object],
    location: str,
    issues: list[EvalIssue],
) -> None:
    clean_text = _EVIDENCE_REF_RE.sub("", text)
    numbers = tuple(dict.fromkeys(_NUMBER_RE.findall(clean_text)))
    if not numbers:
        return
    referenced_payload = "\n".join(
        _evidence_payload(evidence[ref])
        for ref in refs
        if ref in evidence
    )
    for number in numbers:
        if number not in referenced_payload:
            issues.append(
                EvalIssue(
                    severity="severe",
                    code="figure.untraceable",
                    location=location,
                    message=f"Exact figure {number} is not present in referenced evidence.",
                )
            )


def _evidence_payload(item: object) -> str:
    content = getattr(item, "content", None) or ""
    value = getattr(item, "value", None)
    return f"{content}\n{value if value is not None else ''}"


def _validate_measurement_matrix(
    label: str,
    measurements: tuple[EvalMeasurement, ...],
    *,
    expected_profile: RunProfile,
) -> None:
    if not measurements:
        raise ValueError(f"{label} must not be empty")
    repetitions: dict[tuple[str, RunProfile], set[int]] = defaultdict(set)
    seen: set[tuple[str, RunProfile, int]] = set()
    for item in measurements:
        if item.profile is not expected_profile:
            raise ValueError(
                f"{label} requires profile {expected_profile.value}, "
                f"got {item.profile.value}"
            )
        key = (item.case_id, item.profile, item.repetition)
        if key in seen:
            raise ValueError(
                f"{label} contains duplicate measurement "
                f"{item.case_id}/{item.profile.value}/{item.repetition}"
            )
        seen.add(key)
        repetitions[(item.case_id, item.profile)].add(item.repetition)
    incomplete = [
        f"{case_id}/{profile.value}"
        for (case_id, profile), values in repetitions.items()
        if values != {1, 2, 3}
    ]
    if incomplete:
        raise ValueError(
            f"{label} requires repetitions 1, 2, 3 for: {', '.join(incomplete)}"
        )
