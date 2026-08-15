"""Compatibility policy for model-authored Required source dependencies."""

from __future__ import annotations

from collections.abc import Iterable

_INTERNAL_SOURCE_PREFIXES = (
    "ev_",
    "et_",
    "memory:",
    "claim_",
    "question_",
    "calc_",
    "req_",
    "nv_",
    "group_",
    "debate.issue_",
)
_INTERNAL_SOURCE_SEGMENTS = (".claim_", ".section_")


def is_internal_source_reference(value: str) -> bool:
    """Return whether a source-shaped value is an internal object reference."""

    normalized = value.strip().casefold()
    return any(
        normalized.startswith(prefix) for prefix in _INTERNAL_SOURCE_PREFIXES
    ) or any(segment in normalized for segment in _INTERNAL_SOURCE_SEGMENTS)


def partition_source_dependencies(
    values: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split normalized dependencies into external source names and internal refs."""

    normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    internal = tuple(value for value in normalized if is_internal_source_reference(value))
    external = tuple(value for value in normalized if value not in internal)
    return external, internal
