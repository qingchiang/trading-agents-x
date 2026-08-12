"""Shared redaction helpers for persisted or user-visible diagnostics."""

from __future__ import annotations

import re

_JSON_SECRET_RE = re.compile(
    r'(?i)("(?:api[-_ ]?key|authorization|bearer|token|password|secret)"\s*:\s*")[^"]*(")'
)
_AUTHORIZATION_RE = re.compile(
    r"(?i)(\bauthorization\s*[:=]\s*)[^\r\n,;\"]+"
)
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[^\s,;\"]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[-_ ]?key|password|secret|token)(\s*[:=]\s*)[^\s,;\"]+"
)


def sanitize_text(value: str, *, limit: int) -> str:
    """Redact common credential forms before retaining bounded diagnostic text."""
    redacted = _JSON_SECRET_RE.sub(r"\1[REDACTED]\2", value)
    redacted = _AUTHORIZATION_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", redacted)
    return redacted[:limit]
