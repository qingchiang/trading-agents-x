"""Bounded diagnostics without provider messages or execution credentials."""

from __future__ import annotations

import hashlib
import json
import re
import traceback
from pathlib import Path
from typing import Any

from tradingagents.credentials import credential_redactor

_SECRET = re.compile(r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|token)(\s*[:=]\s*)(\S+)")


def diagnostic_text(value: str) -> str:
    return _SECRET.sub(r"\1\2[REDACTED]", credential_redactor()(value))


def exception_diagnostic(error: BaseException) -> dict[str, Any]:
    def type_name(value: BaseException) -> str:
        return diagnostic_text(f"{type(value).__module__}.{type(value).__name__}")[:200]

    result: dict[str, Any] = {"type": type_name(error)}
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and 400 <= status <= 599:
        result["http_status"] = status
    result["frames"] = [
        {"file": diagnostic_text(Path(frame.filename).name)[:160],
         "function": diagnostic_text(frame.name)[:160], "line": frame.lineno}
        for frame in traceback.extract_tb(error.__traceback__)[-8:]
    ]
    causes = []
    seen = {id(error)}
    cause = error.__cause__ or error.__context__
    while cause is not None and id(cause) not in seen and len(causes) < 5:
        seen.add(id(cause))
        causes.append(type_name(cause))
        cause = cause.__cause__ or cause.__context__
    result["cause_types"] = causes
    return result


def requirement_diagnostic(candidate: Any) -> dict[str, Any]:
    """Keep only operands and their formula, never arbitrary candidate fields."""
    if not isinstance(candidate, dict):
        return {"candidate_type": type(candidate).__name__}
    fragment: dict[str, Any] = {}
    for key in ("id", "component_path", "formula", "input_evidence_refs"):
        value = candidate.get(key)
        if isinstance(value, str):
            fragment[key] = diagnostic_text(value)
        elif key == "input_evidence_refs" and isinstance(value, (list, tuple)):
            fragment[key] = [diagnostic_text(v) for v in value if isinstance(v, str)]
    inputs = candidate.get("inputs")
    if isinstance(inputs, (list, tuple)):
        fragment["inputs"] = [
            {key: diagnostic_text(value) if isinstance(value, str) else value
             for key, value in item.items()
             if key in ("name", "value") and isinstance(value, (str, int, float, bool))}
            for item in inputs if isinstance(item, dict)
        ]
    encoded = json.dumps(fragment, ensure_ascii=True, sort_keys=True).encode()
    if len(encoded) > 8192:
        return {"candidate_omitted": "oversize", "candidate_bytes": len(encoded),
                "candidate_digest": hashlib.sha256(encoded).hexdigest()}
    return {"candidate": fragment}
