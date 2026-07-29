"""Stable, typed contracts shared by Python, CLI, worker, and Web API clients."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from tradingagents.application.reporting import order_reports
from tradingagents.dataflows.symbol_utils import (
    crypto_base,
    market_timezone,
    normalize_symbol,
)

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]*$")
_MEMORY_REF_PATTERN = re.compile(
    r"^memory:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
)


def utc_now() -> datetime:
    """Return an aware UTC timestamp for public contracts."""
    return datetime.now(timezone.utc)


class FrozenModel(BaseModel):
    """Base class for immutable public value objects."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RunProfile(str, Enum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class ReportLanguage(str, Enum):
    ENGLISH = "en"
    SIMPLIFIED_CHINESE = "zh-CN"
    JAPANESE = "ja"

    @property
    def prompt_label(self) -> str:
        return {
            ReportLanguage.ENGLISH: "English (en)",
            ReportLanguage.SIMPLIFIED_CHINESE: (
                "Simplified Chinese (简体中文, zh-CN)"
            ),
            ReportLanguage.JAPANESE: "Japanese (日本語, ja)",
        }[self]


_REPORT_LANGUAGE_ALIASES = {
    "en": ReportLanguage.ENGLISH,
    "english": ReportLanguage.ENGLISH,
    "zh-cn": ReportLanguage.SIMPLIFIED_CHINESE,
    "zh-hans": ReportLanguage.SIMPLIFIED_CHINESE,
    "chinese": ReportLanguage.SIMPLIFIED_CHINESE,
    "simplified chinese": ReportLanguage.SIMPLIFIED_CHINESE,
    "简体中文": ReportLanguage.SIMPLIFIED_CHINESE,
    "ja": ReportLanguage.JAPANESE,
    "japanese": ReportLanguage.JAPANESE,
    "日本語": ReportLanguage.JAPANESE,
}

OutputLanguage: TypeAlias = ReportLanguage | str


def normalize_report_language(value: OutputLanguage) -> OutputLanguage:
    """Normalize simple language aliases while preserving custom instructions."""
    if isinstance(value, ReportLanguage):
        return value
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("output language must not be empty")
    return _REPORT_LANGUAGE_ALIASES.get(normalized.casefold(), normalized)


def report_language_value(value: OutputLanguage) -> str:
    """Return the durable request/config representation."""
    return value.value if isinstance(value, ReportLanguage) else value


def report_language_prompt_label(value: OutputLanguage) -> str:
    """Return a prompt-ready label without rewriting custom instructions."""
    return value.prompt_label if isinstance(value, ReportLanguage) else value


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunTrashState(str, Enum):
    ACTIVE = "active"
    TRASHED = "trashed"
    ALL = "all"


class AssetType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


class ResearchRating(str, Enum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class ArtifactGenerationMethod(str, Enum):
    """Auditable method that produced a typed research artifact."""

    TOOL_CALL = "tool_call"
    JSON_MODE = "json_mode"
    RAW_JSON_RECOVERED = "raw_json_recovered"
    JSON_MODE_RECOVERED = "json_mode_recovered"
    SECTIONED_RECOVERY = "sectioned_recovery"


class EvidenceQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class EvidenceTemporalScope(str, Enum):
    """Whether source content is valid at the cutoff or only at retrieval time."""

    POINT_IN_TIME = "point_in_time"
    LIVE_ONLY = "live_only"
    UNKNOWN = "unknown"


class TableDataType(str, Enum):
    """Machine-readable type of values in one research-table column."""

    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    PERCENTAGE = "percentage"
    CURRENCY = "currency"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"


class TableCellKind(str, Enum):
    """Semantic relationship between a table cell and its evidence."""

    DESCRIPTOR = "descriptor"
    OBSERVATION = "observation"
    INFERENCE = "inference"
    DERIVED = "derived"


class EvidenceOrigin(FrozenModel):
    """One source record contributing to an evidence payload."""

    source: str = Field(min_length=1, max_length=200)
    evidence_type: str = Field(min_length=1, max_length=120)
    requested: str = Field(default="unknown", min_length=1)
    effective: str = Field(default="unknown", min_length=1)
    effective_date: date | None = None
    timing: str = Field(default="unknown", min_length=1)
    retrieved_at: str | None = None
    quality: EvidenceQuality = EvidenceQuality.MEDIUM
    fallback: bool = False
    temporal_scope: EvidenceTemporalScope = EvidenceTemporalScope.UNKNOWN


class EvidenceItem(FrozenModel):
    """One immutable, auditable evidence item in a run."""

    ref: str = Field(pattern=r"^ev_[a-f0-9]{12}$")
    source: str = Field(min_length=1, max_length=200)
    evidence_type: str = Field(min_length=1, max_length=120)
    requested_date: date
    effective_date: date | None = None
    available_at: datetime | None = None
    content: str | None = None
    value: float | int | str | None = None
    unit: str | None = None
    quality: EvidenceQuality = EvidenceQuality.MEDIUM
    fallback: bool = False
    origins: tuple[EvidenceOrigin, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        source: str,
        evidence_type: str,
        requested_date: date,
        effective_date: date | None = None,
        available_at: datetime | None = None,
        content: str | None = None,
        value: float | int | str | None = None,
        unit: str | None = None,
        quality: EvidenceQuality = EvidenceQuality.MEDIUM,
        fallback: bool = False,
        origins: tuple[EvidenceOrigin, ...] = (),
        provenance: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        payload = {
            "source": source,
            "evidence_type": evidence_type,
            "requested_date": requested_date.isoformat(),
            "effective_date": effective_date.isoformat() if effective_date else None,
            "available_at": available_at.isoformat() if available_at else None,
            "content": content,
            "value": value,
            "unit": unit,
            "provenance": provenance or {},
        }
        if origins:
            payload["origins"] = [
                origin.model_dump(mode="json") for origin in origins
            ]
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        return cls(
            ref=f"ev_{digest}",
            source=source,
            evidence_type=evidence_type,
            requested_date=requested_date,
            effective_date=effective_date,
            available_at=available_at,
            content=content,
            value=value,
            unit=unit,
            quality=quality,
            fallback=fallback,
            origins=origins,
            provenance=provenance or {},
        )


TableScalar: TypeAlias = str | int | float | bool | None


class ResearchTableColumn(FrozenModel):
    """One stable column in an evidence or research table."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    data_type: TableDataType = TableDataType.TEXT
    unit: str | None = Field(default=None, min_length=1)


class DerivedValue(FrozenModel):
    """Reproducible numeric value derived from cited observations."""

    formula: str = Field(min_length=1)
    inputs: dict[str, int | float] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    unit: str | None = Field(default=None, min_length=1)
    result: int | float

    @field_validator("inputs")
    @classmethod
    def validate_inputs(
        cls,
        value: dict[str, int | float],
    ) -> dict[str, int | float]:
        if any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)
            for key in value
        ):
            raise ValueError("derived input names must be identifiers")
        if any(isinstance(item, bool) for item in value.values()):
            raise ValueError("derived inputs must be numeric")
        return value

    @field_validator("input_evidence_refs")
    @classmethod
    def validate_input_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"ev_[a-f0-9]{12}", ref) for ref in refs):
            raise ValueError("derived inputs must use valid evidence refs")
        return refs


class ResearchTableCell(FrozenModel):
    """One auditable cell in a fact or model-produced research table."""

    raw_value: TableScalar = None
    display_value: str = Field(min_length=1)
    kind: TableCellKind = TableCellKind.OBSERVATION
    evidence_refs: tuple[str, ...] = ()
    derived: DerivedValue | None = None

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"ev_[a-f0-9]{12}", ref) for ref in refs):
            raise ValueError("table cells must use valid evidence refs")
        return refs

    @model_validator(mode="after")
    def validate_semantics(self) -> ResearchTableCell:
        if self.kind is TableCellKind.DESCRIPTOR:
            if self.derived is not None:
                raise ValueError("descriptor cells cannot contain a derivation")
            return self
        if self.kind is TableCellKind.DERIVED:
            if self.derived is None:
                raise ValueError("derived cells require derivation details")
            if not set(self.derived.input_evidence_refs).issubset(
                self.evidence_refs
            ):
                raise ValueError(
                    "derived cell refs must include every derivation input ref"
                )
            if self.raw_value != self.derived.result:
                raise ValueError(
                    "derived cell raw value must equal the saved result"
                )
            return self
        if self.derived is not None:
            raise ValueError(
                "only derived cells may contain derivation details"
            )
        if not self.evidence_refs:
            raise ValueError(
                "observations and inferences require evidence refs"
            )
        return self


class ResearchTableRow(FrozenModel):
    """One stable row keyed by the table's public column identifiers."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    cells: dict[str, ResearchTableCell] = Field(min_length=1)


def _validate_table_shape(
    *,
    columns: tuple[ResearchTableColumn, ...],
    rows: tuple[ResearchTableRow, ...],
) -> None:
    column_keys = tuple(column.key for column in columns)
    if len(column_keys) != len(set(column_keys)):
        raise ValueError("table column keys must be unique")
    row_ids = tuple(row.id for row in rows)
    if len(row_ids) != len(set(row_ids)):
        raise ValueError("table row IDs must be unique")
    expected = set(column_keys)
    for row in rows:
        if set(row.cells) != expected:
            raise ValueError(
                f"table row {row.id} cells must exactly match its columns"
            )


class EvidenceTable(FrozenModel):
    """A complete fact table deterministically extracted from source evidence."""

    id: str = Field(pattern=r"^et_[a-f0-9]{12}$")
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    columns: tuple[ResearchTableColumn, ...] = Field(min_length=1)
    rows: tuple[ResearchTableRow, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    source_format: Literal["structured", "markdown", "csv"]

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"ev_[a-f0-9]{12}", ref) for ref in refs):
            raise ValueError("evidence tables must use valid evidence refs")
        return refs

    @model_validator(mode="after")
    def validate_table(self) -> EvidenceTable:
        _validate_table_shape(columns=self.columns, rows=self.rows)
        table_refs = set(self.evidence_refs)
        for row in self.rows:
            for cell in row.cells.values():
                if cell.kind is not TableCellKind.OBSERVATION:
                    raise ValueError(
                        "deterministic evidence tables contain observations only"
                    )
                if not set(cell.evidence_refs).issubset(table_refs):
                    raise ValueError(
                        "evidence table cell refs must belong to the table"
                    )
        return self

    @classmethod
    def create(
        cls,
        *,
        title: str,
        purpose: str,
        columns: tuple[ResearchTableColumn, ...],
        rows: tuple[ResearchTableRow, ...],
        evidence_refs: tuple[str, ...],
        source_format: Literal["structured", "markdown", "csv"],
    ) -> EvidenceTable:
        payload = {
            "title": title,
            "purpose": purpose,
            "columns": [
                column.model_dump(mode="json") for column in columns
            ],
            "rows": [row.model_dump(mode="json") for row in rows],
            "evidence_refs": list(dict.fromkeys(evidence_refs)),
            "source_format": source_format,
        }
        identity = {
            "title": title,
            "purpose": purpose,
            "columns": payload["columns"],
            "rows": [
                {
                    "id": row.id,
                    "cells": {
                        key: {
                            "raw_value": cell.raw_value,
                            "display_value": cell.display_value,
                            "kind": cell.kind.value,
                            "derived": (
                                cell.derived.model_dump(
                                    mode="json",
                                    exclude={"input_evidence_refs"},
                                )
                                if cell.derived is not None
                                else None
                            ),
                        }
                        for key, cell in row.cells.items()
                    },
                }
                for row in rows
            ],
            "source_format": source_format,
        }
        digest = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:12]
        return cls(id=f"et_{digest}", **payload)


class ResearchTable(FrozenModel):
    """A cited comparison, interpretation, or scenario table in a report."""

    id: str = Field(pattern=r"^rt_[a-z0-9][a-z0-9_.-]*$")
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    columns: tuple[ResearchTableColumn, ...] = Field(min_length=1)
    rows: tuple[ResearchTableRow, ...] = Field(min_length=1)
    source_table_id: str | None = Field(
        default=None,
        pattern=r"^et_[a-f0-9]{12}$",
    )
    total_source_rows: int | None = Field(default=None, ge=1)
    source_row_ids: tuple[str, ...] = ()

    @field_validator("source_row_ids")
    @classmethod
    def validate_source_row_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not re.fullmatch(r"[a-z][a-z0-9_.-]*", row_id)
            for row_id in value
        ):
            raise ValueError("research table source row IDs are invalid")
        return value

    @model_validator(mode="after")
    def validate_table(self) -> ResearchTable:
        _validate_table_shape(columns=self.columns, rows=self.rows)
        if (self.source_table_id is None) != (
            self.total_source_rows is None
        ):
            raise ValueError(
                "source table ID and total source rows must be supplied together"
            )
        if (
            self.total_source_rows is not None
            and self.total_source_rows < len(self.rows)
        ):
            raise ValueError(
                "total source rows cannot be less than displayed rows"
            )
        if self.source_table_id is None and self.source_row_ids:
            raise ValueError(
                "source row IDs require a source evidence table"
            )
        if self.source_table_id is not None:
            if len(self.source_row_ids) != len(self.rows):
                raise ValueError(
                    "source table views require one source row ID per row"
                )
            if len(self.source_row_ids) != len(set(self.source_row_ids)):
                raise ValueError("source row IDs must be unique")
        return self


class EvidenceBundle(FrozenModel):
    """Versioned evidence snapshot shared by every agent in one run."""

    version: Literal["3"] = "3"
    instrument: str
    analysis_date: date
    items: tuple[EvidenceItem, ...]
    tables: tuple[EvidenceTable, ...] = ()
    sealed_at: datetime = Field(default_factory=utc_now)
    digest: str | None = None

    @model_validator(mode="after")
    def validate_bundle(self) -> EvidenceBundle:
        refs = [item.ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("evidence refs must be unique")
        for item in self.items:
            if item.effective_date and item.effective_date > self.analysis_date:
                raise ValueError(
                    f"{item.ref} effective_date is after the analysis cutoff"
                )
            if item.available_at:
                if item.available_at.utcoffset() is None:
                    raise ValueError(
                        f"{item.ref} available_at must include a timezone"
                    )
                available_date = item.available_at.astimezone(
                    market_timezone(self.instrument)
                ).date()
                if available_date > self.analysis_date:
                    raise ValueError(
                        f"{item.ref} available_at is after the analysis cutoff"
                    )
        table_ids = [table.id for table in self.tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("evidence table IDs must be unique")
        valid_refs = set(refs)
        for table in self.tables:
            if not set(table.evidence_refs).issubset(valid_refs):
                raise ValueError(
                    f"{table.id} contains refs outside this evidence bundle"
                )
        serialized_items = [item.model_dump(mode="json") for item in self.items]
        canonical = json.dumps(
            {
                "items": serialized_items,
                "tables": [
                    table.model_dump(mode="json") for table in self.tables
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        calculated = hashlib.sha256(canonical.encode()).hexdigest()
        if self.digest is None:
            object.__setattr__(self, "digest", calculated)
        elif self.digest != calculated:
            raise ValueError("evidence bundle digest does not match its items")
        return self


class AnalystClaimType(str, Enum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    FORECAST = "forecast"


class AnalystClaim(FrozenModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    kind: AnalystClaimType
    statement: str = Field(min_length=1)
    implication: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"ev_[a-f0-9]{12}", ref) for ref in refs):
            raise ValueError("claims must use valid evidence refs")
        return refs


class AnalystSection(FrozenModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    title: str = Field(min_length=1)
    narrative: str = Field(min_length=1)
    table_ids: tuple[str, ...] = ()

    @field_validator("table_ids")
    @classmethod
    def validate_table_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        ids = tuple(dict.fromkeys(value))
        if any(
            not re.fullmatch(
                r"(?:et_[a-f0-9]{12}|rt_[a-z0-9][a-z0-9_.-]*)",
                table_id,
            )
            for table_id in ids
        ):
            raise ValueError("sections contain an invalid table ID")
        return ids


class ResearchWarning(FrozenModel):
    """Structured, plain-text warning suitable for APIs and audit exports."""

    code: str = Field(default="legacy.warning", pattern=r"^[a-z0-9_.-]+$")
    message: str = Field(min_length=1, max_length=2000)
    evidence_ref: str | None = Field(
        default=None,
        pattern=r"^ev_[a-f0-9]{12}$",
    )
    source: str | None = Field(default=None, max_length=200)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: Any) -> str:
        text = str(value)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"(\*\*|__|`)", "", text)
        return " ".join(text.split()).strip()


def _coerce_warnings(value: Any) -> tuple[ResearchWarning, ...]:
    if value is None:
        return ()
    items = (value,) if isinstance(value, (str, dict, ResearchWarning)) else value
    warnings = []
    for item in items:
        if isinstance(item, ResearchWarning):
            warning = item
        elif isinstance(item, str):
            warning = ResearchWarning(message=item)
        else:
            warning = ResearchWarning.model_validate(item)
        warnings.append(warning)
    return tuple(dict.fromkeys(warnings))


class AnalystReport(FrozenModel):
    """Rich, typed analyst hand-off shared by users and downstream agents."""

    analyst: Literal["market", "social", "news", "fundamentals"]
    executive_summary: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    claims: tuple[AnalystClaim, ...] = Field(min_length=1)
    sections: tuple[AnalystSection, ...] = Field(min_length=1)
    tables: tuple[ResearchTable, ...] = ()
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = Field(min_length=1)
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"ev_[a-f0-9]{12}", ref) for ref in refs):
            raise ValueError("reports must use valid evidence refs")
        return refs

    @model_validator(mode="after")
    def validate_structure(self) -> AnalystReport:
        claim_ids = tuple(claim.id for claim in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("analyst claim IDs must be unique")
        section_ids = tuple(section.id for section in self.sections)
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("analyst section IDs must be unique")
        table_ids = tuple(table.id for table in self.tables)
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("research table IDs must be unique")
        referenced_table_ids = {
            table_id
            for section in self.sections
            for table_id in section.table_ids
        }
        if not set(table_ids).issubset(referenced_table_ids):
            raise ValueError(
                "every research table must be placed in an analyst section"
            )
        return self


class PerspectiveReview(FrozenModel):
    role: str
    thesis: str
    claim_rebuttals: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    new_evidence_refs: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()


class ResearchDecision(FrozenModel):
    """Research-only conclusion; deliberately excludes account-level advice."""

    rating: ResearchRating
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    evidence_refs: tuple[str, ...] = ()
    memory_refs: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    time_horizon: str

    @field_validator("memory_refs")
    @classmethod
    def validate_memory_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not _MEMORY_REF_PATTERN.fullmatch(ref) for ref in refs):
            raise ValueError("memory refs must use the memory:<run_id> format")
        return refs


class MemoryOutcome(FrozenModel):
    """Completed five-or-more-interval feedback for one past decision."""

    benchmark: str
    observation_start: date | None = None
    observation_end: date | None = None
    holding_intervals: int = Field(ge=5)
    raw_return: float
    alpha_return: float


class MemoryRecord(FrozenModel):
    """One auditable memory item supplied to a research decision node."""

    ref: str
    run_id: str
    scope: Literal["same_ticker", "same_market"]
    ticker: str
    market: str | None = None
    analysis_date: date
    decision: ResearchDecision | None = None
    outcome: MemoryOutcome | None = None
    reflection: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_record(self) -> MemoryRecord:
        if not _MEMORY_REF_PATTERN.fullmatch(self.ref):
            raise ValueError("memory ref must use the memory:<run_id> format")
        if self.ref != f"memory:{self.run_id}":
            raise ValueError("memory ref must identify its run_id")
        if self.scope == "same_ticker":
            if self.decision is None or self.outcome is None:
                raise ValueError(
                    "same-ticker memory requires decision and outcome"
                )
        elif self.decision is not None or self.outcome is not None:
            raise ValueError(
                "same-market memory must contain reflection-only feedback"
            )
        return self

    def prompt_text(self, max_chars: int = 2000) -> str:
        """Render one bounded block without turning memory into evidence."""
        parts = [
            f"REF: {self.ref}",
            f"SCOPE: {self.scope}",
            (
                f"PAST RUN: {self.analysis_date} | {self.ticker} | "
                f"{self.market or 'unknown market'}"
            ),
        ]
        if self.decision is not None:
            parts.append(
                "PAST DECISION:\n"
                + json.dumps(
                    self.decision.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        if self.outcome is not None:
            parts.append(
                "OBSERVED OUTCOME:\n"
                + json.dumps(
                    self.outcome.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        parts.append(f"REFLECTION:\n{self.reflection}")
        rendered = "\n".join(parts)
        if max_chars <= 0:
            return ""
        if len(rendered) <= max_chars:
            return rendered
        if max_chars == 1:
            return "…"
        return rendered[: max_chars - 1] + "…"


class MemoryContext(FrozenModel):
    """Deterministic, bounded historical feedback for one current run."""

    version: Literal["1"] = "1"
    instrument: str
    market: str | None = None
    items: tuple[MemoryRecord, ...] = ()

    @model_validator(mode="after")
    def validate_context(self) -> MemoryContext:
        refs = [item.ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("memory refs must be unique")
        instrument = self.instrument.casefold()
        for item in self.items:
            if (
                item.scope == "same_ticker"
                and item.ticker.casefold() != instrument
            ):
                raise ValueError(
                    "same-ticker memory must match the current instrument"
                )
            if item.scope == "same_market" and (
                item.ticker.casefold() == instrument
                or self.market is None
                or item.market != self.market
            ):
                raise ValueError(
                    "same-market memory must be another instrument "
                    "in the current market"
                )
        return self

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.ref for item in self.items)

    def prompt_text(
        self,
        *,
        max_chars: int = 12_000,
        item_max_chars: int = 2_000,
    ) -> str:
        if not self.items or max_chars <= 0 or item_max_chars <= 0:
            return ""
        separators = 2 * (len(self.items) - 1)
        available = max(0, max_chars - separators)
        per_item = min(item_max_chars, available // len(self.items))
        if per_item <= 0:
            return ""
        return "\n\n".join(
            item.prompt_text(per_item) for item in self.items
        )[:max_chars]


ResearchArtifactContent = AnalystReport | PerspectiveReview | ResearchDecision


def _artifact_content_type(content: ResearchArtifactContent) -> str:
    if isinstance(content, AnalystReport):
        return "analyst_report"
    if isinstance(content, PerspectiveReview):
        return "perspective_review"
    return "research_decision"


class ResearchArtifactDraft(FrozenModel):
    """Typed graph output awaiting application-owned persistence metadata."""

    node: str = Field(min_length=1, max_length=160)
    stage: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    role: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    round: int = Field(default=0, ge=0)
    schema_version: Literal["1"] = "1"
    prompt_version: str = Field(
        default="research-v1",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    generation_method: ArtifactGenerationMethod
    content: ResearchArtifactContent

    @property
    def content_type(self) -> str:
        return _artifact_content_type(self.content)

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            self.content.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class ResearchArtifact(FrozenModel):
    """Durable, typed output from one visible research stage."""

    id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    attempt: int = Field(ge=1)
    stage: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    role: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    round: int = Field(default=0, ge=0)
    schema_version: Literal["1"] = "1"
    prompt_version: str = Field(
        default="research-v1",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    generation_method: ArtifactGenerationMethod
    content: ResearchArtifactContent
    created_at: datetime

    @property
    def content_type(self) -> str:
        return _artifact_content_type(self.content)


class NodeMetrics(FrozenModel):
    """Resource usage attributed to one research graph node."""

    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)


class RunMetrics(FrozenModel):
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    node_metrics: dict[str, NodeMetrics] = Field(default_factory=dict)


class AnalysisRequest(FrozenModel):
    ticker: str = Field(min_length=1, max_length=64)
    analysis_date: date
    asset_type: AssetType | None = None
    profile: RunProfile = RunProfile.STANDARD
    analysts: tuple[
        Literal["market", "social", "news", "fundamentals"], ...
    ] = ("market", "social", "news", "fundamentals")
    llm_provider: str | None = None
    quick_model: str | None = None
    deep_model: str | None = None
    quick_reasoning_effort: str | None = None
    deep_reasoning_effort: str | None = None
    output_language: OutputLanguage | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        canonical = normalize_symbol(value)
        if not canonical:
            raise ValueError("ticker must not be empty")
        if not _SYMBOL_PATTERN.fullmatch(canonical):
            raise ValueError("ticker contains unsupported characters")
        return canonical

    @field_validator("analysts")
    @classmethod
    def validate_analysts(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one analyst must be selected")
        if len(value) != len(set(value)):
            raise ValueError("analysts must not contain duplicates")
        order = ("market", "social", "news", "fundamentals")
        return tuple(key for key in order if key in value)

    @field_validator("output_language", mode="before")
    @classmethod
    def normalize_output_language(
        cls,
        value: OutputLanguage | None,
    ) -> OutputLanguage | None:
        if value is None:
            return None
        return normalize_report_language(value)

    @model_validator(mode="after")
    def infer_asset_type(self) -> AnalysisRequest:
        inferred = AssetType.CRYPTO if crypto_base(self.ticker) else AssetType.STOCK
        if self.asset_type is None:
            object.__setattr__(self, "asset_type", inferred)
        elif self.asset_type != inferred and inferred is AssetType.CRYPTO:
            raise ValueError("known crypto symbols must use asset_type='crypto'")
        if inferred is AssetType.CRYPTO and "fundamentals" in self.analysts:
            compatible = tuple(
                analyst for analyst in self.analysts if analyst != "fundamentals"
            )
            if not compatible:
                raise ValueError(
                    "crypto analysis requires a non-fundamentals analyst"
                )
            object.__setattr__(self, "analysts", compatible)
        return self


class RunEvent(FrozenModel):
    run_id: str
    sequence: int = Field(ge=1)
    attempt: int = Field(ge=1)
    event_type: str
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AnalysisResult(FrozenModel):
    run_id: str
    status: RunStatus
    instrument: str
    instrument_name: str | None = None
    reports: dict[str, AnalystReport | str]
    decision: ResearchDecision | None
    evidence: EvidenceBundle | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("reports")
    @classmethod
    def order_public_reports(
        cls,
        value: dict[str, AnalystReport | str],
    ) -> dict[str, AnalystReport | str]:
        return order_reports(value)

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)


class RunView(FrozenModel):
    id: str
    source_run_id: str | None = None
    instrument_name: str | None = None
    status: RunStatus
    request: AnalysisRequest
    config_snapshot: dict[str, Any]
    attempt: int
    cancel_requested: bool
    error_code: str | None = None
    error_message: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trashed_at: datetime | None = None
    updated_at: datetime


class RunPage(FrozenModel):
    items: tuple[RunView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class RecentInstrument(FrozenModel):
    ticker: str
    instrument_name: str | None = None
    last_used_at: datetime


class RunExport(FrozenModel):
    """Versioned, self-contained durable run export."""

    schema_version: Literal["1"] = "1"
    run: RunView
    result: AnalysisResult
    evidence: EvidenceBundle | None = None
    artifacts: tuple[ResearchArtifact, ...] = ()
