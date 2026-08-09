"""Versioned contracts and deterministic qualification for outcome feedback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

METHOD_CATEGORY = "short_term_relative_return"
METHOD_VERSION = "short_term_relative_return.v1"
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
    status: str
    reasons: tuple[str, ...]
    candidate: dict[str, Any]
    applicability: dict[str, Any]


def reflection_candidate_lesson(reflection: str) -> str | None:
    match = _METHOD_LESSON_RE.search(reflection.strip())
    if match is None:
        return None
    lesson = match.group(1).strip()
    return lesson or None


def qualify_reflection(
    *,
    reflection: str,
    decision_id: int,
    revision_id: str | None,
    decision_rating: str,
    decision_thesis: str,
    decision_cutoff: date,
    observation_start: date,
    observation_end: date,
    data_available_at: datetime,
    generated_at: datetime,
    qualified_at: datetime,
    ticker: str,
    market: str | None,
    method_category: str,
    horizon_limit: str,
) -> FeedbackQualification:
    """Qualify a generated lesson without treating its prose as research truth."""
    reflection_text = reflection.strip()
    text = reflection_candidate_lesson(reflection_text) or ""
    candidate = {
        "schema_version": "1",
        "lesson": text,
        "source_decision_id": decision_id,
        "source_revision_id": revision_id,
        "method_category": method_category,
        "horizon_limit": horizon_limit,
    }
    applicability = {
        "schema_version": "1",
        "instrument": ticker,
        "market": market,
        "research_stages": ["analysis_methodology"],
        "research_domains": ["cross_domain"],
        "method_category": method_category,
        "horizon": "short_term",
    }
    reasons: list[str] = []
    if not reflection_text or len(reflection_text) > 12_000 or not text:
        reasons.append("schema_invalid")
    if decision_id <= 0:
        reasons.append("source_decision_missing")
    if method_category != METHOD_CATEGORY:
        reasons.append("method_category_invalid")
    if not horizon_limit:
        reasons.append("horizon_limit_missing")
    if observation_start <= decision_cutoff or observation_end < observation_start:
        reasons.append("observation_window_not_after_decision")
    if data_available_at > qualified_at or generated_at > qualified_at:
        reasons.append("point_in_time_availability_invalid")
    if decision_rating and re.search(
        rf"(?i)(?:rating\s*[:=]?\s*)?\b{re.escape(decision_rating)}\b", text
    ):
        reasons.append("contains_old_rating")
    normalized_thesis = " ".join(decision_thesis.split())
    if len(normalized_thesis) >= 20 and normalized_thesis.casefold() in text.casefold():
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
        status="eligible" if not reasons else "ineligible",
        reasons=tuple(dict.fromkeys(reasons)),
        candidate=candidate,
        applicability=applicability,
    )
