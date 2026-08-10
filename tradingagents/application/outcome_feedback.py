"""Versioned contracts and deterministic qualification for outcome feedback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

METHOD_CATEGORY = "short_term_relative_return"
METHOD_VERSION = "short_term_relative_return.v1"
QUALIFICATION_POLICY_VERSION = "outcome_feedback_qualification.v1"
PRICE_SEMANTICS = "exchange_local_daily_close"
ADJUSTMENT_SEMANTICS = "split_and_dividend_adjusted"
HORIZON_LIMIT = (
    "Five completed aligned intervals provide short-term relative-return "
    "feedback only and do not prove or disprove a medium- or long-horizon thesis."
)
OBSERVATION_LIMITATIONS = (
    HORIZON_LIMIT,
    "Relative return is an ex-post price observation, not causal Evidence.",
)

_PRICE_TARGET_RE = re.compile(
    r"(?i)\b(price\s+target|target\s+price|目标价|目標株価)\b"
)
_CURRENT_FACT_RE = re.compile(
    r"(?i)\b(currently|right now|now|as of today|today(?:'s)?|现时|当前事实|現在)\b"
)
_EVIDENCE_CLAIM_RE = re.compile(
    r"(?i)\b(evidence (?:shows|proves|confirms|establishes)|"
    r"source (?:shows|confirms)|证据(?:表明|证明)|証拠(?:は|が))"
)
_EXECUTION_RE = re.compile(
    r"(?i)\b(buy|sell|entry|stop[- ]?loss|take[- ]?profit|position size|"
    r"order|买入|卖出|止损|仓位|購入|売却|損切り|ポジション)\b"
)
_METHOD_LESSON_RE = re.compile(r"(?is)(?:^|\n)Method lesson:\s*(.+?)\s*$")


@dataclass(frozen=True)
class FeedbackQualification:
    status: OutcomeFeedbackStatus
    reasons: tuple[str, ...]
    qualification_policy_version: str
    candidate: dict[str, Any]
    applicability: dict[str, Any]


class OutcomeObservationStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class OutcomeReflectionStatus(StrEnum):
    PENDING = "pending"
    GENERATED = "generated"
    INVALID = "invalid"
    RETRYABLE_FAILURE = "retryable_failure"


class OutcomeFeedbackStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    RETIRED = "retired"


@dataclass(frozen=True)
class FeedbackSource:
    decision_id: int
    revision_id: str | None
    decision_rating: str
    decision_thesis: str
    decision_cutoff: date
    revision_cutoff: date | None
    ticker: str
    market: str | None

    @property
    def cutoff(self) -> date:
        return self.revision_cutoff or self.decision_cutoff


@dataclass(frozen=True)
class ObservationQualificationInput:
    start: date
    end: date
    data_available_at: datetime
    method_category: str
    horizon_limit: str


@dataclass(frozen=True)
class ReflectionQualificationInput:
    text: str
    generated_at: datetime


def reflection_candidate_lesson(reflection: str) -> str | None:
    match = _METHOD_LESSON_RE.search(reflection.strip())
    if match is None:
        return None
    lesson = match.group(1).strip()
    return lesson or None


def _contains_thesis_text(lesson: str, thesis: str) -> bool:
    normalized_lesson = " ".join(lesson.casefold().split())
    normalized_thesis = " ".join(thesis.casefold().split())
    if len(normalized_thesis) >= 20 and normalized_thesis in normalized_lesson:
        return True
    lesson_tokens = re.findall(r"\w+", normalized_lesson)
    thesis_tokens = re.findall(r"\w+", normalized_thesis)
    if len(lesson_tokens) >= 4 and len(thesis_tokens) >= 4:
        lesson_windows = {
            tuple(lesson_tokens[index : index + 4])
            for index in range(len(lesson_tokens) - 3)
        }
        thesis_windows = {
            tuple(thesis_tokens[index : index + 4])
            for index in range(len(thesis_tokens) - 3)
        }
        if lesson_windows & thesis_windows:
            return True
    compact_lesson = "".join(re.findall(r"\w", normalized_lesson))
    compact_thesis = "".join(re.findall(r"\w", normalized_thesis))
    return len(compact_thesis) >= 8 and any(
        compact_thesis[index : index + 8] in compact_lesson
        for index in range(len(compact_thesis) - 7)
    )


def qualify_reflection(
    *,
    source: FeedbackSource,
    observation: ObservationQualificationInput,
    reflection: ReflectionQualificationInput,
    qualified_at: datetime,
) -> FeedbackQualification:
    """Qualify a generated lesson without treating its prose as research truth."""
    reflection_text = reflection.text.strip()
    text = reflection_candidate_lesson(reflection_text) or ""
    candidate = {
        "schema_version": "1",
        "lesson": text,
        "source_decision_id": source.decision_id,
        "source_revision_id": source.revision_id,
        "method_category": observation.method_category,
        "horizon_limit": observation.horizon_limit,
    }
    applicability = {
        "schema_version": "1",
        "scope": "instrument",
        "instrument": source.ticker,
        "market": source.market,
        "research_stages": ["analysis_methodology"],
        "research_domains": ["cross_domain"],
        "method_category": observation.method_category,
        "horizon": "short_term",
    }
    reasons: list[str] = []
    if not reflection_text or len(reflection_text) > 12_000 or not text:
        reasons.append("schema_invalid")
    if source.decision_id <= 0:
        reasons.append("source_decision_missing")
    if observation.method_category != METHOD_CATEGORY:
        reasons.append("method_category_invalid")
    if not observation.horizon_limit:
        reasons.append("horizon_limit_missing")
    if (
        observation.start < source.cutoff
        or observation.end <= source.cutoff
        or observation.end < observation.start
    ):
        reasons.append("observation_window_not_after_decision")
    if (
        observation.data_available_at > qualified_at
        or reflection.generated_at > qualified_at
    ):
        reasons.append("point_in_time_availability_invalid")
    if source.decision_rating and re.search(
        rf"(?i)(?:rating\s*[:=]?\s*)?\b{re.escape(source.decision_rating)}\b", text
    ):
        reasons.append("contains_old_rating")
    if _contains_thesis_text(text, source.decision_thesis):
        reasons.append("contains_thesis_text")
    for pattern, reason in (
        (_PRICE_TARGET_RE, "contains_price_target"),
        (_CURRENT_FACT_RE, "contains_current_factual_assertion"),
        (_EVIDENCE_CLAIM_RE, "contains_evidence_claim"),
        (_EXECUTION_RE, "contains_execution_advice"),
    ):
        if pattern.search(text):
            reasons.append(reason)
    return FeedbackQualification(
        status=(
            OutcomeFeedbackStatus.ELIGIBLE
            if not reasons
            else OutcomeFeedbackStatus.INELIGIBLE
        ),
        reasons=tuple(dict.fromkeys(reasons)),
        qualification_policy_version=QUALIFICATION_POLICY_VERSION,
        candidate=candidate,
        applicability=applicability,
    )
