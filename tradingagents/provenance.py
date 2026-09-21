"""Deterministic data-provenance metadata and quality checks.

Vendor results carry a small versioned HTML comment. The comment is visible to
the analyst model but hidden by Markdown renderers; application graph nodes
convert it into typed evidence before ToolMessages are cleared. No provenance
is inferred from model prose.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict

from langchain_core.messages import BaseMessage, ToolMessage

from tradingagents.domain.data import (
    EvidenceSpan,
    ProvenanceRecord,
    TemporalScopeName,
)
from tradingagents.domain.data_quality import temporal_scope_from_records

_MARKER_PREFIX = "tradingagents-provenance:v1"
_MARKER_RE = re.compile(
    rf"<!--\s*{re.escape(_MARKER_PREFIX)}\s+(\{{.*?\}})\s*-->",
    re.DOTALL,
)
_SPAN_PREFIX = "tradingagents-evidence-span:v1"
_SPAN_END = f"<!-- /{_SPAN_PREFIX} -->"
_SPAN_RE = re.compile(
    rf"<!--\s*{re.escape(_SPAN_PREFIX)}\s+(\{{.*?\}})\s*-->"
    rf"(.*?)<!--\s*/{re.escape(_SPAN_PREFIX)}\s*-->",
    re.DOTALL,
)
_SPAN_MARKER_RE = re.compile(
    rf"<!--\s*/?{re.escape(_SPAN_PREFIX)}(?:\s+\{{.*?\}})?\s*-->",
    re.DOTALL,
)

def provenance_marker(record: ProvenanceRecord) -> str:
    """Serialize public provenance fields into a versioned HTML comment."""
    payload = {key: value for key, value in asdict(record).items() if value is not None}
    return f"<!-- {_MARKER_PREFIX} {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))} -->"


def attach_provenance(text: str, *records: ProvenanceRecord) -> str:
    """Prepend records to a textual vendor result without changing its rendering."""
    if not isinstance(text, str) or not records:
        return text
    existing = set(_MARKER_RE.findall(text))
    markers = []
    for record in records:
        marker = provenance_marker(record)
        payload = _MARKER_RE.search(marker).group(1)
        if payload not in existing:
            markers.append(marker)
            existing.add(payload)
    if not markers:
        return text
    return "\n".join([*markers, text]) if text else "\n".join(markers)


def attach_evidence_span(
    text: str,
    *,
    temporal_scope: TemporalScopeName,
) -> str:
    """Wrap one body so composite tool responses retain temporal boundaries."""
    if temporal_scope not in {"point_in_time", "live_only", "unknown"}:
        raise ValueError(f"unsupported temporal scope: {temporal_scope!r}")
    payload = json.dumps(
        {"temporal_scope": temporal_scope},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"<!-- {_SPAN_PREFIX} {payload} -->{text}{_SPAN_END}"


def extract_evidence_spans(text: str) -> list[EvidenceSpan]:
    """Split explicit live-only blocks from the remaining point-in-time body."""
    if not isinstance(text, str):
        return []
    matches = list(_SPAN_RE.finditer(text))
    if not matches:
        return []

    explicit: list[EvidenceSpan] = []
    remainder_parts: list[str] = []
    cursor = 0
    for match in matches:
        remainder_parts.append(text[cursor : match.start()])
        cursor = match.end()
        try:
            scope = json.loads(match.group(1)).get("temporal_scope", "unknown")
        except (AttributeError, json.JSONDecodeError):
            scope = "unknown"
        if scope not in {"point_in_time", "live_only", "unknown"}:
            scope = "unknown"
        raw_content = match.group(2).strip()
        explicit.append(
            EvidenceSpan(
                content=strip_provenance_markers(raw_content).strip() or None,
                records=tuple(extract_provenance(raw_content)),
                temporal_scope=scope,
            )
        )
    remainder_parts.append(text[cursor:])
    remainder = "\n".join(
        part.strip() for part in remainder_parts if part.strip()
    )
    remainder_records = tuple(extract_provenance(remainder))
    remainder_content = strip_provenance_markers(remainder).strip() or None
    if remainder_records or remainder_content:
        explicit.insert(
            0,
            EvidenceSpan(
                content=remainder_content,
                records=remainder_records,
                temporal_scope=temporal_scope_from_records(
                    remainder_records
                ),
            ),
        )
    return explicit


def strip_provenance_markers(text: str) -> str:
    """Remove machine metadata while preserving the human-readable vendor body."""
    if not isinstance(text, str):
        return text
    return _SPAN_MARKER_RE.sub("", _MARKER_RE.sub("", text)).lstrip("\n")


def extract_provenance(value: str | Iterable[BaseMessage]) -> list[ProvenanceRecord]:
    """Read only structured markers from text or ToolMessages.

    Malformed or future-version markers are ignored.  This deliberately does not
    parse headings or natural language, so an LLM cannot fabricate provenance by
    merely mentioning a vendor in its report.
    """
    if isinstance(value, str):
        texts = [value]
    else:
        texts = [message.content for message in value if isinstance(message, ToolMessage)]

    records: list[ProvenanceRecord] = []
    seen: set[ProvenanceRecord] = set()
    for content in texts:
        if not isinstance(content, str):
            continue
        for raw in _MARKER_RE.findall(content):
            try:
                payload = json.loads(raw)
                record = ProvenanceRecord(
                    evidence=str(payload["evidence"]),
                    source=str(payload["source"]),
                    requested=str(payload.get("requested", "unknown")),
                    effective=str(payload.get("effective", "unknown")),
                    timing=str(payload.get("timing", "unknown")),
                    retrieved_at=(
                        str(payload["retrieved_at"])
                        if payload.get("retrieved_at") is not None
                        else None
                    ),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if record not in seen:
                records.append(record)
                seen.add(record)
    return records
