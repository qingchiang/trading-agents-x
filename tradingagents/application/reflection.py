"""Versioned structured Outcome Reflection generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_LANGUAGE_INSTRUCTIONS = {
    "English (en)": "Write every text field in English.",
    "Simplified Chinese (简体中文, zh-CN)": "Write every text field in Simplified Chinese.",
    "Japanese (日本語, ja)": "Write every text field in Japanese.",
    "English": "Write every text field in English.",
    "Chinese": "Write every text field in Simplified Chinese.",
    "Japanese": "Write every text field in Japanese.",
}

OUTCOME_REFLECTION_SCHEMA_VERSION = "outcome_reflection.v1"
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token|password)(\s*[:=]\s*)[^\s,;]+"
)
_JSON_SECRET_RE = re.compile(
    r'(?i)("(?:api[_-]?key|authorization|bearer|token|password)"\s*:\s*")[^"]*(")'
)


class OutcomeReflectionDraft(BaseModel):
    """The prospective, model-authored portion of an Outcome Reflection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directional_assessment: Literal["consistent", "mixed", "inconsistent"]
    source_decision_evidence_lesson: str = Field(min_length=1, max_length=1_200)
    method_lesson: str = Field(min_length=1, max_length=1_200)
    usage: dict[str, int | float | None] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def reject_model_usage(cls, value: Any) -> Any:
        if isinstance(value, dict) and "usage" in value:
            raise ValueError("usage is application-owned")
        return value

    @property
    def schema_version(self) -> str:
        return OUTCOME_REFLECTION_SCHEMA_VERSION

    @property
    def readable_text(self) -> str:
        return "\n".join(
            (
                f"Directional assessment: {self.directional_assessment}",
                f"Source-decision evidence lesson: {self.source_decision_evidence_lesson}",
                f"Method lesson\n{self.method_lesson}",
            )
        )

    def audit_candidate(self) -> dict[str, str]:
        return {"schema_version": self.schema_version, **self.model_dump()}


@dataclass(frozen=True)
class ReflectionDraftValidationError(ValueError):
    """A candidate failed the local v1 contract and may be repaired once."""

    candidate: str | None
    validation_issues: tuple[str, ...]
    usage: dict[str, int | float | None] = field(default_factory=dict)


class OutcomeReflector:
    """Turn a persisted short-term Observation into a bounded v1 draft."""

    def __init__(self, llm: Any, *, output_language: str = "English"):
        self.llm = llm
        self.output_language = output_language

    def reflect(self, **kwargs: Any) -> OutcomeReflectionDraft:
        return self._generate(**kwargs, repair_candidate=None, validation_issues=())

    def repair(
        self,
        *,
        candidate: str | None,
        validation_issues: tuple[str, ...],
        **kwargs: Any,
    ) -> OutcomeReflectionDraft:
        return self._generate(
            **kwargs,
            repair_candidate=candidate,
            validation_issues=validation_issues,
        )

    def _generate(
        self,
        *,
        decision: str,
        raw_return: float,
        alpha_return: float,
        benchmark: str,
        ticker: str,
        holding_intervals: int,
        observation_start: str,
        observation_end: str,
        repair_candidate: str | None,
        validation_issues: tuple[str, ...],
    ) -> OutcomeReflectionDraft:
        language = _LANGUAGE_INSTRUCTIONS.get(
            self.output_language,
            f"Write every text field in {self.output_language}.",
        )
        system = (
            "You review short-term market feedback on a past research decision. "
            "Return only one JSON object matching outcome_reflection.v1, with exactly "
            "directional_assessment, source_decision_evidence_lesson, and method_lesson. "
            "directional_assessment must be one of consistent, mixed, inconsistent. "
            "Each lesson must be concise, reusable methodological prose. "
            "The application owns the short-horizon limitation; do not restate or "
            "self-certify it. Do not invent causes, targets, position sizes, entry "
            "levels, or account instructions. "
            f"{language}"
        )
        human = (
            f"Instrument: {ticker}\n"
            f"Observation window: {observation_start} to {observation_end} "
            f"({holding_intervals} completed aligned trading intervals)\n"
            f"Raw return: {raw_return:+.1%}\n"
            f"Alpha vs {benchmark}: {alpha_return:+.1%}\n\n"
            f"Stored research decision:\n{decision}"
        )
        if repair_candidate is not None:
            human += (
                "\n\nRepair the prior candidate into the exact JSON contract. "
                f"Validation issues: {', '.join(validation_issues) or 'schema_invalid'}. "
                f"Prior candidate: {repair_candidate[:4_000]}"
            )
        response = self.llm.invoke([("system", system), ("human", human)])
        raw = getattr(response, "content", response)
        candidate = _sanitize_candidate(raw) if isinstance(raw, str) else None
        try:
            payload = json.loads(candidate or "")
            return OutcomeReflectionDraft.model_validate(payload).model_copy(
                update={"usage": _response_usage(response)}
            )
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ReflectionDraftValidationError(
                candidate=candidate,
                validation_issues=_validation_issues(exc),
                usage=_response_usage(response),
            ) from exc


def _validation_issues(error: Exception) -> tuple[str, ...]:
    if isinstance(error, ValidationError):
        return tuple(
            ".".join(str(part) for part in issue["loc"])
            for issue in error.errors()
        )
    if isinstance(error, json.JSONDecodeError):
        return ("json_invalid",)
    return ("candidate_invalid",)


def _response_usage(response: Any) -> dict[str, int | float | None]:
    """Normalize provider-reported accounting without inventing unavailable values."""
    metadata = getattr(response, "usage_metadata", None)
    if not isinstance(metadata, dict):
        response_metadata = getattr(response, "response_metadata", None)
        metadata = response_metadata.get("usage", {}) if isinstance(response_metadata, dict) else {}
    return {
        "input_tokens": _usage_int(metadata, "input_tokens", "prompt_tokens"),
        "output_tokens": _usage_int(metadata, "output_tokens", "completion_tokens"),
        "cache_hit_input_tokens": _usage_int(metadata, "cache_read_input_tokens"),
        "cache_miss_input_tokens": _usage_int(metadata, "cache_creation_input_tokens"),
        "reasoning_output_tokens": _usage_int(metadata, "reasoning_tokens"),
        "provider_reported_cost_usd": _usage_float(metadata, "cost_usd", "cost"),
    }


def _usage_int(metadata: Any, *keys: str) -> int | None:
    if not isinstance(metadata, dict):
        return None
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _usage_float(metadata: Any, *keys: str) -> float | None:
    if not isinstance(metadata, dict):
        return None
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    return None


def _sanitize_candidate(value: str, *, limit: int = 4_000) -> str:
    redacted = _JSON_SECRET_RE.sub(r"\1[REDACTED]\2", value)
    return _SECRET_RE.sub(r"\1\2[REDACTED]", redacted)[:limit]
