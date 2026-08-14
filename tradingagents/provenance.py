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
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Literal

from langchain_core.messages import BaseMessage, ToolMessage

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
_SOURCE_RECORD_PREFIX = "tradingagents-source-record:v1"
_SOURCE_RECORD_RE = re.compile(
    rf"<!--\s*{re.escape(_SOURCE_RECORD_PREFIX)}\s+(\{{.*?\}})\s*-->",
    re.DOTALL,
)
_SOURCE_WATERMARK_PREFIX = "tradingagents-source-watermark:v1"
_SOURCE_WATERMARK_RE = re.compile(
    rf"<!--\s*{re.escape(_SOURCE_WATERMARK_PREFIX)}\s+(\{{.*?\}})\s*-->",
    re.DOTALL,
)

TemporalScopeName = Literal["point_in_time", "live_only", "unknown"]


@dataclass(frozen=True)
class ProvenanceRecord:
    """One auditable evidence block; all fields are intentionally textual."""

    evidence: str
    source: str
    requested: str = "unknown"
    effective: str = "unknown"
    timing: str = "unknown"
    retrieved_at: str | None = None


@dataclass(frozen=True)
class ProvenanceQualityIssue:
    """One deterministic quality issue derived from source metadata."""

    evidence: str
    source: str
    code: str
    reason: str


@dataclass(frozen=True)
class EvidenceSpan:
    """One explicitly bounded body with a shared temporal contract."""

    content: str | None
    records: tuple[ProvenanceRecord, ...]
    temporal_scope: TemporalScopeName


@dataclass(frozen=True)
class SourceObservation:
    """One immutable observed version of a stable record with optional native ID."""

    source: str
    record_id: str
    version_id: str
    status: str
    published_at: str
    available_at: str
    title: str
    availability_basis: str | None = None
    url: str | None = None
    replaces_version_id: str | None = None
    record_kind: Literal["disclosure", "fundamental", "market"] = "disclosure"
    native_record_id: str | None = None
    comparison_key: str | None = None
    change_hint: Literal[
        "new_filing",
        "correction",
        "restatement",
        "accounting_scope_change",
        "unclassifiable",
    ] | None = None
    accounting_scope: str | None = None
    adjustment: str | None = None
    observation_value: float | None = None
    unit: str | None = None
    precision: int | None = None

    def __post_init__(self) -> None:
        if self.status not in {"published", "corrected", "withdrawn", "replaced"}:
            raise ValueError("unsupported Source Record status")
        available = datetime.fromisoformat(self.available_at)
        if available.utcoffset() is None:
            raise ValueError("Source Record available_at requires timezone")
        if not all((self.source, self.record_id, self.version_id, self.title)):
            raise ValueError("Source Record identity and title must not be empty")
        if self.record_kind not in {"disclosure", "fundamental", "market"}:
            raise ValueError("unsupported Source Record kind")
        if self.precision is not None and self.precision < 0:
            raise ValueError("Source Record precision must be non-negative")


@dataclass(frozen=True)
class SourceWatermark:
    """A source-specific collection boundary and its explicit limitations."""

    source: str
    scanned_start: str
    scanned_end: str
    status: str
    temporal_scope: TemporalScopeName = "point_in_time"
    limitations: tuple[str, ...] = ()
    returned_records: int = 0
    reported_records: int | None = None

    def __post_init__(self) -> None:
        start = date.fromisoformat(self.scanned_start)
        end = date.fromisoformat(self.scanned_end)
        if start > end:
            raise ValueError("Source Watermark start must not follow end")
        if self.status not in {"complete", "limited", "unavailable"}:
            raise ValueError("unsupported Source Watermark status")
        if self.temporal_scope not in {"point_in_time", "live_only", "unknown"}:
            raise ValueError("unsupported Source Watermark temporal scope")
        if self.returned_records < 0 or (
            self.reported_records is not None and self.reported_records < 0
        ):
            raise ValueError("Source Watermark record counts must be non-negative")


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


def _attach_machine_records(text: str, prefix: str, records: Iterable[object]) -> str:
    markers = [
        f"<!-- {prefix} "
        f"{json.dumps(asdict(record), ensure_ascii=False, separators=(',', ':'))} -->"
        for record in records
    ]
    return "\n".join([text, *markers]) if markers else text


def attach_source_observations(text: str, *records: SourceObservation) -> str:
    """Append structured Source Record observations outside human-visible prose."""
    return _attach_machine_records(text, _SOURCE_RECORD_PREFIX, records)


def attach_source_watermarks(text: str, *records: SourceWatermark) -> str:
    """Append structured source coverage boundaries outside human-visible prose."""
    return _attach_machine_records(text, _SOURCE_WATERMARK_PREFIX, records)


def _extract_machine_records(text: str, pattern: re.Pattern[str], model):
    records = []
    seen = set()
    if not isinstance(text, str):
        return records
    for raw in pattern.findall(text):
        try:
            payload = json.loads(raw)
            if model is SourceWatermark:
                payload["limitations"] = tuple(payload.get("limitations") or ())
            record = model(**payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if record not in seen:
            records.append(record)
            seen.add(record)
    return records


def extract_source_observations(text: str) -> list[SourceObservation]:
    return _extract_machine_records(text, _SOURCE_RECORD_RE, SourceObservation)


def extract_source_watermarks(text: str) -> list[SourceWatermark]:
    return _extract_machine_records(text, _SOURCE_WATERMARK_RE, SourceWatermark)


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


def temporal_scope_from_records(
    records: Iterable[ProvenanceRecord],
) -> TemporalScopeName:
    """Infer a conservative scope for unwrapped legacy source metadata."""
    scopes = {_temporal_scope_from_record(record) for record in records}
    scopes.discard("unknown")
    return scopes.pop() if len(scopes) == 1 else "unknown"


def _temporal_scope_from_record(record: ProvenanceRecord) -> TemporalScopeName:
    text = " ".join(
        (
            record.evidence,
            record.source,
            record.effective,
            record.timing,
        )
    ).casefold()
    if any(
        token in text
        for token in (
            "live-only",
            "live only",
            "live non-point-in-time",
            "live non point in time",
            "current-only",
            "current snapshot",
            "retrieval-time snapshot",
            "retrieval-time analyst",
            "not historical pit",
            "not point-in-time",
            "non-point-in-time",
        )
    ):
        return "live_only"
    if any(
        token in text
        for token in (
            "point-in-time",
            "date filtered",
            "date-filtered",
            "market-date filtered",
            "trade-date filtered",
            "observation-date filtered",
            "publication-date filtered",
            "publication/update-date filtered",
            "disclosure-date filtered",
            "fiscal period ends",
        )
    ):
        return "point_in_time"
    return "unknown"


def strip_provenance_markers(text: str) -> str:
    """Remove machine metadata while preserving the human-readable vendor body."""
    if not isinstance(text, str):
        return text
    value = strip_source_metadata_markers(text)
    return _SPAN_MARKER_RE.sub("", _MARKER_RE.sub("", value)).lstrip("\n")


def strip_source_metadata_markers(text: str) -> str:
    """Remove Source Record and Watermark markers while retaining provenance."""
    if not isinstance(text, str):
        return text
    return _SOURCE_WATERMARK_RE.sub("", _SOURCE_RECORD_RE.sub("", text)).rstrip()


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
    (("not requested",), "not_requested", "expected evidence was not requested"),
    (
        ("no auditable source metadata",),
        "missing_metadata",
        "no auditable source metadata captured",
    ),
    (("no usable data",), "no_usable_data", "no usable data from configured sources"),
    (("unavailable",), "unavailable", "source unavailable for requested date/window"),
    (("failed",), "retrieval_failed", "source retrieval failed"),
    (("fallback",), "fallback", "fallback source used"),
    (
        ("adjustment provider changed",),
        "adjustment_changed",
        "adjustment provider changed; technical indicators may differ",
    ),
    (
        (
            "non-point-in-time",
            "not point-in-time",
            "not historical pit",
            "non-strict pit",
        ),
        "not_point_in_time",
        "not point-in-time",
    ),
    (("non-vintage",), "non_vintage", "non-vintage series"),
    (("not queried",), "not_queried", "source was not queried"),
    (("truncated",), "truncated", "result set truncated"),
    (("stale",), "stale", "stale data"),
    (("partial",), "partial", "partial coverage"),
    (
        ("future-dated evidence withheld",),
        "future_dated",
        "future-dated evidence withheld",
    ),
)


def _is_successful_empty(timing: str) -> bool:
    """True for a successful query that legitimately produced no evidence."""
    return timing.startswith("available;") and (
        "; no " in timing or "contained no values" in timing
    )


def provenance_quality_issues(
    records: Iterable[ProvenanceRecord],
) -> list[ProvenanceQualityIssue]:
    """Return deterministic warnings for material provenance degradation.

    Routine date filtering and an empty-but-successful news window are not
    warnings. The terms below describe missing evidence, fallback/partial
    coverage, stale/truncated data, or timing that is unsuitable for strict
    historical interpretation.
    """
    issues: list[ProvenanceQualityIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        timing = record.timing.strip()
        timing_search = timing.casefold()
        reasons = list(
            dict.fromkeys(
                (code, label)
                for terms, code, label in _WARNING_RULES
                if any(term in timing_search for term in terms)
            )
        )
        if record.source.strip().casefold() in {"", "unknown", "—"}:
            reasons.append(("unknown_source", "source metadata unknown"))
        if (
            record.effective.strip().casefold() in {"", "unknown", "—"}
            and not _is_successful_empty(timing_search)
        ):
            reasons.append(
                ("unknown_effective", "effective date/window unknown")
            )
        evidence = _escape_cell(record.evidence)
        source = _escape_cell(record.source)
        for code, reason in dict.fromkeys(reasons):
            key = (evidence.casefold(), source.casefold(), reason.casefold())
            if key not in seen:
                issues.append(
                    ProvenanceQualityIssue(
                        evidence=evidence,
                        source=source,
                        code=code,
                        reason=reason,
                    )
                )
                seen.add(key)
    return issues
