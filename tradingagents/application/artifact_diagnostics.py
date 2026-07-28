"""Deterministic diagnostics for historical degraded research artifacts."""

from __future__ import annotations

import json
from typing import Any

from .contracts import (
    ArtifactDiagnostics,
    ArtifactGenerationMethod,
    PerspectiveReview,
    ResearchArtifactContent,
    ResearchDecision,
)

_SENTINEL_VALUES = {
    "n/a",
    "not available",
    "structured decision output was unavailable.",
    "reassess when higher-quality evidence becomes available.",
    "unavailable",
    "unknown",
    "unspecified",
    "unspecified research horizon",
}


def diagnose_artifact(
    content: ResearchArtifactContent,
    generation_method: ArtifactGenerationMethod,
) -> ArtifactDiagnostics:
    """Describe visible legacy degradation without mutating canonical content."""
    parsed_thesis = _parse_nested_json(getattr(content, "thesis", None))
    missing_fields: list[str] = []
    sentinel_fields: list[str] = []
    reason_codes: list[str] = []

    if parsed_thesis is not None:
        reason_codes.append("nested_json_thesis")

    if isinstance(content, PerspectiveReview):
        _record_missing(
            missing_fields,
            thesis=content.thesis,
            claim_rebuttals=content.claim_rebuttals,
            risks=content.risks,
            evidence_refs=content.evidence_refs,
        )
    elif isinstance(content, ResearchDecision):
        _record_missing(
            missing_fields,
            thesis=content.thesis,
            risks=content.risks,
            invalidation_conditions=content.invalidation_conditions,
            time_horizon=content.time_horizon,
            evidence_refs=content.evidence_refs,
        )
        for field, value in (
            ("risks", content.risks),
            ("invalidation_conditions", content.invalidation_conditions),
            ("time_horizon", content.time_horizon),
        ):
            if _is_sentinel(value):
                sentinel_fields.append(field)

    if missing_fields:
        reason_codes.append("missing_structured_fields")
    if sentinel_fields:
        reason_codes.append("fallback_sentinel_fields")

    outer_rating: str | None = None
    nested_rating: str | None = None
    rating_conflict = False
    if isinstance(content, ResearchDecision) and parsed_thesis is not None:
        outer_rating = content.rating.value
        nested = parsed_thesis.get("rating")
        if isinstance(nested, str) and nested.strip():
            nested_rating = nested.strip()
            rating_conflict = nested_rating.casefold() != outer_rating.casefold()
            if rating_conflict:
                reason_codes.append("rating_conflict")

    degraded_output = bool(reason_codes)
    return ArtifactDiagnostics(
        degraded_output=degraded_output,
        legacy_degraded_output=(
            degraded_output
            and generation_method is ArtifactGenerationMethod.LEGACY_UNKNOWN
        ),
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        missing_fields=tuple(dict.fromkeys(missing_fields)),
        sentinel_fields=tuple(dict.fromkeys(sentinel_fields)),
        parsed_thesis=parsed_thesis,
        outer_rating=outer_rating,
        nested_rating=nested_rating,
        rating_conflict=rating_conflict,
        rerun_recommended=degraded_output,
    )


def _parse_nested_json(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate.startswith("{") or not candidate.endswith("}"):
        return None
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _record_missing(target: list[str], **fields: Any) -> None:
    for name, value in fields.items():
        if value is None or value == "" or value == () or value == []:
            target.append(name)


def _is_sentinel(value: Any) -> bool:
    values = value if isinstance(value, (tuple, list)) else (value,)
    normalized = [
        " ".join(str(item).split()).strip().casefold()
        for item in values
        if item is not None
    ]
    return bool(normalized) and all(item in _SENTINEL_VALUES for item in normalized)
