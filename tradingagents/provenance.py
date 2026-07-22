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


_WARNING_RULES = (
    (("not requested",), "expected evidence was not requested"),
    (("no auditable source metadata",), "no auditable source metadata captured"),
    (("no usable data",), "no usable data from configured sources"),
    (("unavailable",), "source unavailable for requested date/window"),
    (("failed",), "source retrieval failed"),
    (("fallback",), "fallback source used"),
    (
        ("adjustment provider changed",),
        "adjustment provider changed; technical indicators may differ",
    ),
    (
        (
            "non-point-in-time",
            "not point-in-time",
            "not historical pit",
            "non-strict pit",
        ),
        "not point-in-time",
    ),
    (("non-vintage",), "non-vintage series"),
    (("not queried",), "source was not queried"),
    (("truncated",), "result set truncated"),
    (("stale",), "stale data"),
    (("partial",), "partial coverage"),
)


def _is_successful_empty(timing: str) -> bool:
    """True for a successful query that legitimately produced no evidence."""
    return timing.startswith("available;") and (
        "; no " in timing or "contained no values" in timing
    )


def _quality_warnings(
    records: Iterable[ProvenanceRecord],
) -> list[tuple[str, str, str]]:
    """Return deterministic warnings for material provenance degradation.

    Routine date filtering and an empty-but-successful news window are not
    warnings. The terms below describe missing evidence, fallback/partial
    coverage, stale/truncated data, or timing that is unsuitable for strict
    historical interpretation.
    """
    warnings: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        timing = record.timing.strip()
        timing_search = timing.casefold()
        reasons = list(
            dict.fromkeys(
                label
                for terms, label in _WARNING_RULES
                if any(term in timing_search for term in terms)
            )
        )
        if record.source.strip().casefold() in {"", "unknown", "—"}:
            reasons.append("source metadata unknown")
        if (
            record.effective.strip().casefold() in {"", "unknown", "—"}
            and not _is_successful_empty(timing_search)
        ):
            reasons.append("effective date/window unknown")
        evidence = _escape_cell(record.evidence)
        source = _escape_cell(record.source)
        for reason in dict.fromkeys(reasons):
            key = (evidence.casefold(), source.casefold(), reason.casefold())
            if key not in seen:
                warnings.append((evidence, source, reason))
                seen.add(key)
    return warnings


def append_provenance_appendix(
    report: str,
    records: Iterable[ProvenanceRecord],
    *,
    expected: Iterable[tuple[str, str]] = (),
    requested_date: str | None = None,
    enabled: bool = True,
) -> str:
    """Append quality warnings and, when enabled, one provenance table.

    ``enabled`` controls the detailed provenance table only. Material quality
    warnings remain visible so disabling the audit appendix cannot hide a
    fallback, unavailable source, timing limitation, or coverage problem.
    """
    report = report if isinstance(report, str) else str(report)
    while _APPENDIX_START in report and _APPENDIX_END in report:
        start = report.index(_APPENDIX_START)
        end = report.index(_APPENDIX_END, start) + len(_APPENDIX_END)
        before = report[:start].rstrip()
        after = report[end:].lstrip()
        report = f"{before}\n\n{after}" if before and after else before or after

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

    warnings = _quality_warnings(deduped)
    if not enabled and not warnings:
        return report

    rows = [_APPENDIX_START, "---", ""]
    if warnings:
        rows.extend(["## Data Quality Warnings", ""])
        rows.extend(
            f"- **{evidence}** (source: {source}): {_escape_cell(reason)}"
            for evidence, source, reason in warnings
        )
        rows.append("")
    if enabled:
        rows.extend(
            [
                "## Data Provenance",
                "",
                "| Evidence | Source | Requested / cutoff | Effective date / window | Timing status |",
                "|---|---|---|---|---|",
            ]
        )
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
