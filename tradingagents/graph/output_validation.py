"""Shared semantic validation helpers for typed research outputs."""

from __future__ import annotations

import json

_STRUCTURED_SENTINELS = {
    "n/a",
    "none",
    "not available",
    "unavailable",
    "unknown",
    "unspecified",
    "unspecified research horizon",
    "不可用",
    "不明",
    "未知",
    "未定",
    "未指定",
    "利用不可",
    "指定なし",
}


def require_text(value: str, *, reject_sentinel: bool = True) -> None:
    """Reject empty, nested-JSON, and optional fallback-sentinel text."""

    text = value.strip()
    if not text or looks_like_json_object(text):
        raise ValueError("structured text field is empty or contains nested JSON")
    normalized = text.casefold().strip(" .。!！?？-_")
    if reject_sentinel and normalized in _STRUCTURED_SENTINELS:
        raise ValueError("structured text field contains a fallback sentinel")


def require_nonempty_texts(values: tuple[str, ...]) -> None:
    """Require a non-empty tuple of meaningful text values."""

    if not values:
        raise ValueError("structured list field must not be empty")
    for value in values:
        require_text(value)


def require_valid_refs(
    refs: tuple[str, ...],
    allowed: set[str],
    *,
    required: bool,
) -> None:
    """Validate evidence references against the sealed run snapshot."""

    if required and not refs:
        raise ValueError("at least one evidence ref is required")
    if len(refs) != len(set(refs)) or any(ref not in allowed for ref in refs):
        raise ValueError("structured output contains an invalid reference")


def looks_like_json_object(value: str) -> bool:
    """Return whether a text field is an accidentally nested JSON object."""

    try:
        return isinstance(json.loads(value), dict)
    except (TypeError, ValueError):
        return False
