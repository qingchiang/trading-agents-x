"""Deterministic data-provenance metadata and report appendix rendering.

Vendor results carry a small versioned HTML comment.  The comment is visible to
the analyst model but hidden by Markdown renderers; analyst nodes parse it before
ToolMessages are cleared and append one human-readable audit table to the report.
No provenance is inferred from model prose.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from langchain_core.messages import BaseMessage, ToolMessage

_MARKER_PREFIX = "tradingagents-provenance:v1"
_MARKER_RE = re.compile(
    rf"<!--\s*{re.escape(_MARKER_PREFIX)}\s+(\{{.*?\}})\s*-->",
    re.DOTALL,
)
_APPENDIX_START = "<!-- tradingagents-data-provenance:start -->"
_APPENDIX_END = "<!-- tradingagents-data-provenance:end -->"


@dataclass(frozen=True)
class ProvenanceRecord:
    """One auditable evidence block; all fields are intentionally textual."""

    evidence: str
    source: str
    requested: str = "unknown"
    effective: str = "unknown"
    timing: str = "unknown"
    retrieved_at: str | None = None


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


def strip_provenance_markers(text: str) -> str:
    """Remove machine metadata while preserving the human-readable vendor body."""
    return _MARKER_RE.sub("", text).lstrip("\n") if isinstance(text, str) else text


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


def _escape_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip() or "—"


def append_provenance_appendix(
    report: str,
    records: Iterable[ProvenanceRecord],
    *,
    expected: Iterable[tuple[str, str]] = (),
    requested_date: str | None = None,
    enabled: bool = True,
) -> str:
    """Append exactly one deterministic English provenance table when enabled."""
    report = report if isinstance(report, str) else str(report)
    if not enabled:
        return report
    if _APPENDIX_START in report and _APPENDIX_END in report:
        start = report.index(_APPENDIX_START)
        end = report.index(_APPENDIX_END, start) + len(_APPENDIX_END)
        report = (report[:start] + report[end:]).rstrip()

    deduped: list[ProvenanceRecord] = []
    seen: set[ProvenanceRecord] = set()
    for record in records:
        if record not in seen:
            deduped.append(record)
            seen.add(record)

    present = {record.evidence for record in deduped}
    for evidence, label in expected:
        if evidence not in present:
            deduped.append(
                ProvenanceRecord(
                    evidence=label,
                    source="—",
                    requested=requested_date or "unknown",
                    effective="—",
                    timing="not requested",
                )
            )

    if not deduped:
        deduped.append(
            ProvenanceRecord(
                evidence="analyst evidence",
                source="unknown",
                requested=requested_date or "unknown",
                effective="unknown",
                timing="no auditable source metadata captured",
            )
        )

    rows = [
        _APPENDIX_START,
        "## Data provenance",
        "",
        "| Evidence | Source | Requested / cutoff | Effective date / window | Timing status |",
        "|---|---|---|---|---|",
    ]
    for record in deduped:
        timing = record.timing
        if record.retrieved_at:
            timing = f"{timing}; retrieved {record.retrieved_at}"
        rows.append(
            "| "
            + " | ".join(
                _escape_cell(value)
                for value in (
                    record.evidence,
                    record.source,
                    record.requested,
                    record.effective,
                    timing,
                )
            )
            + " |"
        )
    rows.append(_APPENDIX_END)
    return f"{report.rstrip()}\n\n" + "\n".join(rows)
