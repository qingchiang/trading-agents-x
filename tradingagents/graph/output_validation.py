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


class OutputValidationError(ValueError):
    """A safe, stable semantic issue that may be shown to a recovery model."""

    def __init__(
        self,
        issue_code: str,
        *,
        issue_codes: tuple[str, ...] = (),
    ):
        self.issue_code = issue_code
        self.issue_codes = tuple(dict.fromkeys(issue_codes or (issue_code,)))
        super().__init__(issue_code)


def require_text(value: str, *, reject_sentinel: bool = True) -> None:
    """Reject empty, nested-JSON, and optional fallback-sentinel text."""

    text = value.strip()
    if not text or looks_like_json_object(text):
        raise OutputValidationError("text.empty_or_nested_json")
    normalized = text.casefold().strip(" .。!！?？-_")
    if reject_sentinel and normalized in _STRUCTURED_SENTINELS:
        raise OutputValidationError("text.fallback_sentinel")


def require_nonempty_texts(values: tuple[str, ...]) -> None:
    """Require a non-empty tuple of meaningful text values."""

    if not values:
        raise OutputValidationError("list.empty")
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
        raise OutputValidationError("refs.missing")
    if len(refs) != len(set(refs)) or any(ref not in allowed for ref in refs):
        raise OutputValidationError("refs.invalid")


def looks_like_json_object(value: str) -> bool:
    """Return whether a text field is an accidentally nested JSON object."""

    try:
        return isinstance(json.loads(value), dict)
    except (TypeError, ValueError):
        return False
