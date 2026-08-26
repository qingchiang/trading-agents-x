"""Stable, typed contracts shared by Python, CLI, worker, and Web API clients."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from tradingagents.application.reporting import order_reports
from tradingagents.dataflows.symbol_utils import (
    is_supported_equity_symbol,
    market_timezone,
    normalize_symbol,
    unsupported_crypto_base,
)

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]*$")
_EVIDENCE_REF_PATTERN = re.compile(r"^ev_[a-f0-9]{12}$")
_RESEARCH_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_DECISION_COMPONENT_PATH_PATTERN = re.compile(
    r"^(?:executive_summary|thesis|catalysts\.\d+|risks\.\d+|"
    r"invalidation_conditions\.\d+|"
    r"scenarios\.(?:base|bull|bear)\.(?:outcome|core_assumptions\.\d+)|"
    r"risk_review_adjustments\.\d+\.explanation)$"
)


def _unique_evidence_refs(value: tuple[str, ...]) -> tuple[str, ...]:
    refs = tuple(dict.fromkeys(value))
    if any(not _EVIDENCE_REF_PATTERN.fullmatch(ref) for ref in refs):
        raise ValueError("invalid evidence reference")
    return refs


def _unique_research_ids(value: tuple[str, ...]) -> tuple[str, ...]:
    ids = tuple(dict.fromkeys(value))
    if any(not _RESEARCH_ID_PATTERN.fullmatch(item) for item in ids):
        raise ValueError("invalid research identifier")
    return ids


def utc_now() -> datetime:
    """Return an aware UTC timestamp for public contracts."""
    return datetime.now(UTC)


def _deeply_frozen_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _deeply_frozen_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_deeply_frozen_value(item) for item in value)
    return value


def _deeply_frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _deeply_frozen_value(item) for key, item in value.items()})


class FrozenModel(BaseModel):
    """Base class for immutable public value objects."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _StableStrEnum(StrEnum):
    """Use the standard string enum while retaining the prior ``str()`` contract."""

    __str__ = Enum.__str__


class RunProfile(_StableStrEnum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class ReportLanguage(_StableStrEnum):
    ENGLISH = "en"
    SIMPLIFIED_CHINESE = "zh-CN"
    JAPANESE = "ja"

    @property
    def prompt_label(self) -> str:
        return {
            ReportLanguage.ENGLISH: "English (en)",
            ReportLanguage.SIMPLIFIED_CHINESE: ("Simplified Chinese (简体中文, zh-CN)"),
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

type OutputLanguage = ReportLanguage | str


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


class RunStatus(_StableStrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunTrashState(_StableStrEnum):
    ACTIVE = "active"
    TRASHED = "trashed"
    ALL = "all"


class AssetType(_StableStrEnum):
    STOCK = "stock"


class ResearchRating(_StableStrEnum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class DebateImportance(_StableStrEnum):
    CRITICAL = "critical"
    MATERIAL = "material"
    SECONDARY = "secondary"


class RiskReviewDisposition(_StableStrEnum):
    RETAINED = "retained"
    MODIFIED = "modified"
    REJECTED = "rejected"


class ResearchScenarioKind(_StableStrEnum):
    BASE = "base"
    BULL = "bull"
    BEAR = "bear"


class ScenarioReferenceCategory(_StableStrEnum):
    """Research purpose of a non-valuation scenario reference range."""

    TECHNICAL = "technical"
    HISTORICAL = "historical"
    ANALYST_CONSENSUS = "analyst_consensus"
    FUNDAMENTAL = "fundamental"
    OTHER = "other"


class NumericAuditComponentType(_StableStrEnum):
    """Stable component identity for localized numeric audit omissions."""

    APPENDIX = "appendix"
    CALCULATION = "calculation"
    SCENARIO_RANGE = "scenario_range"
    VALUATION = "valuation"
    MARKET_REFERENCE = "market_reference"
    DECISION_CLAIM = "decision_claim"


class NumericAuditStatus(_StableStrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


class NumericAuditAppendixStatus(_StableStrEnum):
    COMPLETE = "complete"
    RECOVERED = "recovered"
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"


class NumericCalculationStatus(_StableStrEnum):
    VERIFIED = "verified"
    INVALID = "invalid"
    MISSING = "missing"


class NumericDisplayStatus(_StableStrEnum):
    MATCHED = "matched"
    APPROXIMATELY_MATCHED = "approximately_matched"
    MISMATCHED = "mismatched"
    NOT_CHECKED = "not_checked"


class NumericDisplayScale(_StableStrEnum):
    """Deterministic scale applied only when comparing reader-facing values."""

    BASE = "base"
    THOUSAND = "thousand"
    TEN_THOUSAND = "ten_thousand"
    MILLION = "million"
    HUNDRED_MILLION = "hundred_million"
    BILLION = "billion"
    TRILLION = "trillion"


class NumericAuditPhase(_StableStrEnum):
    INITIAL = "initial"
    REPAIR = "repair"


class ArtifactGenerationMethod(_StableStrEnum):
    """Auditable method that produced a typed research artifact."""

    TOOL_CALL = "tool_call"
    TOOL_CALL_RECOVERED = "tool_call_recovered"
    JSON_MODE = "json_mode"
    RAW_JSON_RECOVERED = "raw_json_recovered"
    JSON_MODE_RECOVERED = "json_mode_recovered"
    SECTIONED_RECOVERY = "sectioned_recovery"
    MARKDOWN_AUDITED = "markdown_audited"
    MARKDOWN_AUDIT_INCOMPLETE = "markdown_audit_incomplete"


class NumericAuditSnapshot(FrozenModel):
    """One sanitized failed numeric serializer candidate."""

    phase: NumericAuditPhase
    method: ArtifactGenerationMethod
    reason_code: str = Field(pattern=r"^[a-z0-9_.-]+$")
    validation_issues: tuple[str, ...] = ()
    schema_valid: bool
    candidate: dict[str, Any] | None = None
    candidate_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    candidate_omitted: Literal["oversize"] | None = None

    @field_validator("validation_issues")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        issues = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"[a-z0-9_.-]+", item) for item in issues):
            raise ValueError("numeric audit issues must be stable codes")
        return issues


class NumericAuditOmission(FrozenModel):
    component_path: str = Field(pattern=r"^[a-z0-9_.-]+$")
    component_type: NumericAuditComponentType
    scenario_kind: ResearchScenarioKind | None = None
    reference_label: str | None = Field(default=None, min_length=1, max_length=200)
    issue_codes: tuple[str, ...] = Field(min_length=1)

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        issues = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"[a-z0-9_.-]+", item) for item in issues):
            raise ValueError("numeric audit issues must be stable codes")
        return issues


class NumericRequirementCheck(FrozenModel):
    """Auditable comparison between a stated value and a canonical result."""

    requirement_id: str = Field(pattern=r"^req_[a-z0-9][a-z0-9_.-]*$")
    calculation_id: str | None = Field(
        default=None,
        pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$",
    )
    component_path: str = Field(pattern=_DECISION_COMPONENT_PATH_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=200)
    stated_value: int | float
    fraction_digits: int = Field(ge=0, le=8)
    unit: str = Field(min_length=1, max_length=32)
    display_scale: NumericDisplayScale = NumericDisplayScale.BASE
    formula: str = Field(min_length=1)
    inputs: dict[str, int | float] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = ()
    canonical_result: int | float | None = None
    comparison_result: int | float | None = None
    comparison_difference: int | float | None = None
    rounded_stated_value: int | float | None = None
    rounded_canonical_result: int | float | None = None
    calculation_status: NumericCalculationStatus
    display_status: NumericDisplayStatus
    issue_codes: tuple[str, ...] = ()

    @field_validator("inputs")
    @classmethod
    def validate_inputs(
        cls,
        value: dict[str, int | float],
    ) -> dict[str, int | float]:
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) for key in value):
            raise ValueError("calculation input names must be identifiers")
        if any(isinstance(item, bool) for item in value.values()):
            raise ValueError("calculation inputs must be numeric")
        return value

    @field_validator("input_evidence_refs")
    @classmethod
    def validate_input_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("date_evidence_refs")
    @classmethod
    def validate_date_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_date_ref_subset(self) -> NumericRequirementCheck:
        if not set(self.date_evidence_refs).issubset(self.input_evidence_refs):
            raise ValueError("calculation date refs must belong to input evidence refs")
        return self

    @field_validator("issue_codes")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        issues = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"[a-z0-9_.-]+", item) for item in issues):
            raise ValueError("numeric audit issues must be stable codes")
        return issues

    @model_validator(mode="after")
    def validate_status_fields(self) -> NumericRequirementCheck:
        if self.calculation_status is NumericCalculationStatus.VERIFIED:
            if self.calculation_id is None or self.canonical_result is None:
                raise ValueError("verified calculations require an ID and result")
            comparison_fields = (
                self.comparison_result,
                self.comparison_difference,
            )
            if any(item is not None for item in comparison_fields) and any(
                item is None for item in comparison_fields
            ):
                raise ValueError("display comparison fields must be all present or all absent")
            if self.display_status is NumericDisplayStatus.NOT_CHECKED:
                raise ValueError("verified calculations require a display comparison")
            if self.rounded_stated_value is None or self.rounded_canonical_result is None:
                raise ValueError("checked displays require both rounded values")
        elif self.display_status is not NumericDisplayStatus.NOT_CHECKED:
            raise ValueError("invalid or missing calculations cannot compare display")
        return self


class DecisionNumericAuditAppendix(FrozenModel):
    """Decision calculation comparisons and unverified numeric proposals."""

    status: NumericAuditAppendixStatus
    requirement_checks: tuple[NumericRequirementCheck, ...] = ()
    snapshots: tuple[NumericAuditSnapshot, ...] = Field(max_length=2)
    omitted_components: tuple[NumericAuditOmission, ...] = ()


class MarketReferenceBasis(_StableStrEnum):
    OBSERVED = "observed"
    INTERPRETED = "interpreted"
    DERIVED = "derived"


class EvidenceQuality(_StableStrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class EvidenceTemporalScope(_StableStrEnum):
    """Whether source content is valid at the cutoff or only at retrieval time."""

    POINT_IN_TIME = "point_in_time"
    LIVE_ONLY = "live_only"
    UNKNOWN = "unknown"


class TableDataType(_StableStrEnum):
    """Machine-readable type of values in one deterministic evidence column."""

    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    PERCENT = "percent"
    CURRENCY = "currency"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"


class MeasurementKind(_StableStrEnum):
    """Semantic measurement family owned by deterministic data producers."""

    CURRENCY = "currency"
    PERCENT = "percent"
    RATIO = "ratio"
    INDEX = "index"
    QUANTITY = "quantity"
    COUNT = "count"
    BASIS_POINTS = "basis_points"
    UNITLESS = "unitless"
    UNKNOWN = "unknown"


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
    measurement_kind: MeasurementKind = MeasurementKind.UNKNOWN
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
        measurement_kind: MeasurementKind = MeasurementKind.UNKNOWN,
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
            "measurement_kind": measurement_kind.value,
            "unit": unit,
            "provenance": provenance or {},
        }
        if origins:
            payload["origins"] = [origin.model_dump(mode="json") for origin in origins]
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
            measurement_kind=measurement_kind,
            unit=unit,
            quality=quality,
            fallback=fallback,
            origins=origins,
            provenance=provenance or {},
        )


type TableScalar = str | int | float | bool | None


class EvidenceTableColumn(FrozenModel):
    """One machine-readable column in a deterministic source table."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    data_type: TableDataType = TableDataType.TEXT
    measurement_kind: MeasurementKind = MeasurementKind.UNKNOWN
    unit: str | None = Field(default=None, min_length=1)


class EvidenceTableCell(FrozenModel):
    """One raw value in a deterministic source table."""

    # Keep the union inline so Pydantic preserves the existing OpenAPI shape;
    # a named PEP 695 alias is emitted as a separate schema component.
    raw_value: str | int | float | bool | None = None
    measurement_kind: MeasurementKind | None = None
    unit: str | None = Field(default=None, min_length=1)
    source_refs: tuple[str, ...] = ()

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class EvidenceTableRow(FrozenModel):
    """One stable source row with provenance only where it differs by row."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    cells: dict[str, EvidenceTableCell] = Field(min_length=1)
    source_refs: tuple[str, ...] = ()

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


def _validate_evidence_table_shape(
    *,
    columns: tuple[EvidenceTableColumn, ...],
    rows: tuple[EvidenceTableRow, ...],
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
            raise ValueError(f"table row {row.id} cells must exactly match its columns")


class EvidenceTable(FrozenModel):
    """A complete fact table deterministically extracted from source evidence."""

    id: str = Field(pattern=r"^et_[a-f0-9]{12}$")
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    columns: tuple[EvidenceTableColumn, ...] = Field(min_length=1)
    rows: tuple[EvidenceTableRow, ...] = Field(min_length=1)
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
        _validate_evidence_table_shape(columns=self.columns, rows=self.rows)
        table_refs = set(self.evidence_refs)
        for row in self.rows:
            if not set(row.source_refs).issubset(table_refs):
                raise ValueError("evidence table row refs must belong to the table")
            for cell in row.cells.values():
                if not set(cell.source_refs).issubset(table_refs):
                    raise ValueError("evidence table cell refs must belong to the table")
        return self

    @classmethod
    def create(
        cls,
        *,
        title: str,
        purpose: str,
        columns: tuple[EvidenceTableColumn, ...],
        rows: tuple[EvidenceTableRow, ...],
        evidence_refs: tuple[str, ...],
        source_format: Literal["structured", "markdown", "csv"],
    ) -> EvidenceTable:
        payload = {
            "title": title,
            "purpose": purpose,
            "columns": [column.model_dump(mode="json") for column in columns],
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
                            "measurement_kind": (
                                cell.measurement_kind.value
                                if cell.measurement_kind is not None
                                else None
                            ),
                            "unit": cell.unit,
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


class EvidenceBundle(FrozenModel):
    """Versioned evidence snapshot shared by every agent in one run."""

    version: Literal["8"] = "8"
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
                raise ValueError(f"{item.ref} effective_date is after the analysis cutoff")
            if item.available_at:
                if item.available_at.utcoffset() is None:
                    raise ValueError(f"{item.ref} available_at must include a timezone")
                available_date = item.available_at.astimezone(
                    market_timezone(self.instrument)
                ).date()
                if available_date > self.analysis_date:
                    raise ValueError(f"{item.ref} available_at is after the analysis cutoff")
        table_ids = [table.id for table in self.tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("evidence table IDs must be unique")
        valid_refs = set(refs)
        for table in self.tables:
            if not set(table.evidence_refs).issubset(valid_refs):
                raise ValueError(f"{table.id} contains refs outside this evidence bundle")
        serialized_items = [item.model_dump(mode="json") for item in self.items]
        canonical = json.dumps(
            {
                "items": serialized_items,
                "tables": [table.model_dump(mode="json") for table in self.tables],
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


class AnalystClaimType(_StableStrEnum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    FORECAST = "forecast"


class ClaimImportance(_StableStrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"


class ReportAuditStatus(_StableStrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class ReportSection(FrozenModel):
    """A deterministic heading extracted from the human-readable report."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    title: str = Field(min_length=1)
    anchor: str = Field(min_length=1)
    source_refs: tuple[str, ...] = ()

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class KeyClaim(FrozenModel):
    """A decision-relevant assertion extracted from a readable report."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    kind: AnalystClaimType
    importance: ClaimImportance
    statement: str = Field(min_length=1)
    implication: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"ev_[a-f0-9]{12}", ref) for ref in refs):
            raise ValueError("key claims must use valid evidence refs")
        return refs


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
    """Readable analyst report with a deliberately small audit envelope."""

    analyst: Literal["market", "social", "news", "fundamentals"]
    markdown: str = Field(min_length=1)
    report_sections: tuple[ReportSection, ...] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    key_claims: tuple[KeyClaim, ...] = ()
    source_refs: tuple[str, ...] = ()
    audit_status: ReportAuditStatus
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_structure(self) -> AnalystReport:
        claim_ids = tuple(claim.id for claim in self.key_claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("analyst claim IDs must be unique")
        section_ids = tuple(section.id for section in self.report_sections)
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("analyst section IDs must be unique")
        if any(claim.section_id not in set(section_ids) for claim in self.key_claims):
            raise ValueError("key claims must identify an existing report section")
        used_refs = {ref for claim in self.key_claims for ref in claim.evidence_refs}
        used_refs.update(ref for section in self.report_sections for ref in section.source_refs)
        if not used_refs.issubset(self.source_refs):
            raise ValueError("report source refs must include claim and section refs")
        if self.audit_status is ReportAuditStatus.COMPLETE:
            if not any(claim.importance is ClaimImportance.PRIMARY for claim in self.key_claims):
                raise ValueError("complete report audit requires a primary claim")
            if any(not claim.evidence_refs for claim in self.key_claims):
                raise ValueError("complete report audit requires cited claims")
        return self


class DecisionBrief(FrozenModel):
    """Readable Final reasoning persisted before strict decision serialization."""

    markdown: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)


class ResearchCase(FrozenModel):
    """A readable constructive or skeptical research case."""

    role: Literal["bull", "bear"]
    markdown: str = Field(min_length=1)


class DebateIssue(FrozenModel):
    """One material question used only for graph routing and navigation."""

    id: str = Field(pattern=r"^debate\.issue_[a-z0-9][a-z0-9_.-]*$")
    question: str = Field(min_length=1)
    importance: DebateImportance


class DebateAgenda(FrozenModel):
    """Prioritized shallow agenda derived from the two readable cases."""

    summary: str = Field(min_length=1)
    issues: tuple[DebateIssue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_agenda(self) -> DebateAgenda:
        issue_ids = tuple(issue.id for issue in self.issues)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("debate issue IDs must be unique")
        return self


class RebuttalReview(FrozenModel):
    """One readable response plus the issue IDs needed by graph control."""

    role: Literal["bull", "bear"]
    round: int = Field(ge=1)
    markdown: str = Field(min_length=1)
    addressed_issue_ids: tuple[str, ...] = Field(min_length=1)
    open_issue_ids: tuple[str, ...] = ()

    @field_validator("addressed_issue_ids", "open_issue_ids")
    @classmethod
    def validate_issue_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_research_ids(value)


class IssueDisposition(FrozenModel):
    """A judge routing result without duplicating the readable rationale."""

    issue_id: str = Field(pattern=r"^debate\.issue_[a-z0-9][a-z0-9_.-]*$")
    status: Literal["upheld", "rejected", "unresolved"]


class JudgeDraft(FrozenModel):
    """Readable preliminary judgment with shallow issue dispositions."""

    markdown: str = Field(min_length=1)
    preliminary_rating: ResearchRating | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    issue_dispositions: tuple[IssueDisposition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_draft(self) -> JudgeDraft:
        issue_ids = tuple(item.issue_id for item in self.issue_dispositions)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("judge dispositions must use unique issue IDs")
        return self


class RiskReview(FrozenModel):
    """A readable challenge with only navigation metadata typed."""

    role: Literal["integrated", "aggressive", "neutral", "conservative"]
    markdown: str = Field(min_length=1)
    challenged_issue_ids: tuple[str, ...] = ()
    unresolved_issue_ids: tuple[str, ...] = ()

    @field_validator("challenged_issue_ids", "unresolved_issue_ids")
    @classmethod
    def validate_issue_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_research_ids(value)


class NumericTemporalBasis(_StableStrEnum):
    """How the application determined the date of a formal numeric value."""

    POINT_IN_TIME = "point_in_time"
    LIVE_SNAPSHOT = "live_snapshot"


class EvidenceValueLocator(FrozenModel):
    """Exact Evidence Ledger location for a directly observed scalar."""

    evidence_ref: str = Field(pattern=r"^ev_[a-f0-9]{12}$")
    table_id: str | None = Field(default=None, pattern=r"^et_[a-f0-9]{12}$")
    row_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]*$")
    column: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def validate_table_location(self) -> EvidenceValueLocator:
        table_parts = (self.table_id, self.row_id, self.column)
        if any(part is not None for part in table_parts) and not all(
            part is not None for part in table_parts
        ):
            raise ValueError("table-backed evidence values require table_id, row_id, and column")
        return self


class AuditedRangeEndpoint(FrozenModel):
    """One evidence-backed endpoint of a scenario or valuation range."""

    value: float
    basis: MarketReferenceBasis
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = Field(min_length=1)
    source_locator: EvidenceValueLocator | None = None
    calculation_id: str | None = None
    as_of_date: date
    temporal_basis: NumericTemporalBasis = NumericTemporalBasis.POINT_IN_TIME

    @field_validator("evidence_refs", "date_evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("calculation_id")
    @classmethod
    def validate_calculation_id(cls, value: str | None) -> str | None:
        if value is not None and not _RESEARCH_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid calculation identifier")
        return value

    @model_validator(mode="after")
    def validate_basis(self) -> AuditedRangeEndpoint:
        if not set(self.date_evidence_refs).issubset(self.evidence_refs):
            raise ValueError("date evidence refs must be included in endpoint refs")
        if self.basis is MarketReferenceBasis.OBSERVED:
            if self.source_locator is None:
                raise ValueError("observed endpoint requires an Evidence locator")
            if self.calculation_id:
                raise ValueError("observed endpoint must not reference a calculation")
            if self.source_locator.evidence_ref not in self.evidence_refs:
                raise ValueError("observed endpoint refs must include its locator ref")
        elif self.basis is MarketReferenceBasis.INTERPRETED:
            if self.source_locator is not None or self.calculation_id:
                raise ValueError("interpreted endpoint must not claim a locator or calculation")
        elif self.basis is MarketReferenceBasis.DERIVED:
            if not self.calculation_id:
                raise ValueError("derived endpoint requires a calculation")
            if self.source_locator is not None:
                raise ValueError("derived endpoint must not claim an observed locator")
        return self


class ScenarioReferenceRange(FrozenModel):
    """A scenario-specific reference band, not necessarily a valuation."""

    category: ScenarioReferenceCategory
    label: str = Field(min_length=1, max_length=120)
    low: AuditedRangeEndpoint
    high: AuditedRangeEndpoint
    measurement_kind: MeasurementKind = MeasurementKind.UNKNOWN
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    interpretation: str = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> ScenarioReferenceRange:
        if self.high.value <= self.low.value:
            raise ValueError("reference range high must be greater than low")
        return self


class ResearchScenario(FrozenModel):
    kind: ResearchScenarioKind
    core_assumptions: tuple[str, ...] = Field(min_length=1)
    outcome: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    reference_ranges: tuple[ScenarioReferenceRange, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class ValuationAssessment(FrozenModel):
    method: str = Field(min_length=1)
    low: AuditedRangeEndpoint
    high: AuditedRangeEndpoint
    measurement_kind: MeasurementKind
    unit: str = Field(min_length=1, max_length=32)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_valuation(self) -> ValuationAssessment:
        if self.low.basis is not MarketReferenceBasis.DERIVED:
            raise ValueError("valuation low endpoint must be derived")
        if self.high.basis is not MarketReferenceBasis.DERIVED:
            raise ValueError("valuation high endpoint must be derived")
        if self.measurement_kind is MeasurementKind.UNKNOWN:
            raise ValueError("valuation measurement must be known")
        if self.high.value < self.low.value:
            raise ValueError("valuation high must be >= low")
        return self

    @property
    def calculation_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item
                for item in (self.low.calculation_id, self.high.calculation_id)
                if item is not None
            )
        )

    @property
    def input_evidence_refs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.low.evidence_refs, *self.high.evidence_refs)))

    @property
    def as_of_date(self) -> date:
        return max(self.low.as_of_date, self.high.as_of_date)


class MarketReferenceLevel(FrozenModel):
    label: str = Field(min_length=1, max_length=120)
    value: float
    measurement_kind: MeasurementKind = MeasurementKind.UNKNOWN
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    as_of_date: date
    interpretation: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = Field(min_length=1)
    basis: MarketReferenceBasis = MarketReferenceBasis.OBSERVED
    source_locator: EvidenceValueLocator | None = None
    calculation_ids: tuple[str, ...] = ()
    temporal_basis: NumericTemporalBasis = NumericTemporalBasis.POINT_IN_TIME

    @field_validator("evidence_refs", "date_evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("calculation_ids")
    @classmethod
    def validate_calculation_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_research_ids(value)

    @model_validator(mode="after")
    def validate_basis(self) -> MarketReferenceLevel:
        if not set(self.date_evidence_refs).issubset(self.evidence_refs):
            raise ValueError("date evidence refs must be included in market reference refs")
        if self.basis is MarketReferenceBasis.OBSERVED:
            if self.source_locator is None:
                raise ValueError("observed market reference requires an Evidence locator")
            if self.calculation_ids:
                raise ValueError("observed market reference cannot use calculations")
            if self.source_locator.evidence_ref not in self.evidence_refs:
                raise ValueError("market reference refs must include its locator ref")
        elif self.basis is MarketReferenceBasis.INTERPRETED:
            if self.source_locator is not None or self.calculation_ids:
                raise ValueError(
                    "interpreted market reference cannot claim direct or derived audit"
                )
        elif self.basis is MarketReferenceBasis.DERIVED:
            if not self.calculation_ids:
                raise ValueError("derived market reference requires a calculation")
            if self.source_locator is not None:
                raise ValueError("derived market reference cannot claim a locator")
        return self


class RiskReviewAdjustment(FrozenModel):
    source_role: Literal[
        "integrated",
        "aggressive",
        "neutral",
        "conservative",
    ]
    disposition: RiskReviewDisposition
    subject: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class DecisionCalculationUse(FrozenModel):
    """One readable decision component that relies on a calculation."""

    component_path: str = Field(pattern=_DECISION_COMPONENT_PATH_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=200)


class CalculationRecord(FrozenModel):
    """A decision-critical calculation, not a presentation-table cell."""

    id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")
    formula: str = Field(min_length=1)
    inputs: dict[str, int | float] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = ()
    result: int | float
    unit: str = Field(min_length=1, max_length=32)
    as_of_date: date
    temporal_basis: NumericTemporalBasis = NumericTemporalBasis.POINT_IN_TIME
    limitations: tuple[str, ...] = Field(min_length=1)
    decision_uses: tuple[DecisionCalculationUse, ...] = ()

    @field_validator("inputs")
    @classmethod
    def validate_inputs(
        cls,
        value: dict[str, int | float],
    ) -> dict[str, int | float]:
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) for key in value):
            raise ValueError("calculation input names must be identifiers")
        if any(isinstance(item, bool) for item in value.values()):
            raise ValueError("calculation inputs must be numeric")
        return value

    @field_validator("input_evidence_refs")
    @classmethod
    def validate_input_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("date_evidence_refs")
    @classmethod
    def validate_date_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_date_ref_subset(self) -> CalculationRecord:
        if not set(self.date_evidence_refs).issubset(self.input_evidence_refs):
            raise ValueError("calculation date refs must belong to input evidence refs")
        return self


class ResearchDecision(FrozenModel):
    """Research-only conclusion; deliberately excludes account-level advice."""

    rating: ResearchRating
    confidence: float = Field(ge=0.0, le=1.0)
    executive_summary: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = Field(min_length=1)
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    unresolved_questions: tuple[str, ...] = ()
    time_horizon: str = Field(min_length=1)
    scenarios: tuple[ResearchScenario, ...] = Field(
        min_length=3,
        max_length=3,
    )
    valuation_assessment: ValuationAssessment | None = None
    market_reference_levels: tuple[MarketReferenceLevel, ...] = ()
    calculation_records: tuple[CalculationRecord, ...] = ()
    risk_review_adjustments: tuple[RiskReviewAdjustment, ...] = ()
    numeric_audit_status: NumericAuditStatus | None = None

    @model_validator(mode="before")
    @classmethod
    def merge_nested_evidence_refs(cls, value: Any) -> Any:
        """Make the top-level evidence index a deterministic nested-ref union."""
        if not isinstance(value, dict):
            return value
        # Retained pre-redesign Decisions may still contain ``memory_refs``.
        # Drop that retired field while hydrating the current core contract so
        # Execution History remains readable without exposing Memory again.
        value = {key: item for key, item in value.items() if key != "memory_refs"}
        merged = list(value.get("evidence_refs") or ())
        for scenario in value.get("scenarios") or ():
            merged.extend(_field_value(scenario, "evidence_refs") or ())
            for reference_range in _field_value(scenario, "reference_ranges") or ():
                for endpoint_name in ("low", "high"):
                    endpoint = _field_value(reference_range, endpoint_name)
                    merged.extend(_field_value(endpoint, "evidence_refs") or ())
        valuation = value.get("valuation_assessment")
        if valuation is not None:
            for endpoint_name in ("low", "high"):
                endpoint = _field_value(valuation, endpoint_name)
                merged.extend(_field_value(endpoint, "evidence_refs") or ())
        for level in value.get("market_reference_levels") or ():
            merged.extend(_field_value(level, "evidence_refs") or ())
        for calculation in value.get("calculation_records") or ():
            merged.extend(_field_value(calculation, "input_evidence_refs") or ())
        for adjustment in value.get("risk_review_adjustments") or ():
            merged.extend(_field_value(adjustment, "evidence_refs") or ())
        return {**value, "evidence_refs": tuple(dict.fromkeys(merged))}

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_scenario_set(self) -> ResearchDecision:
        scenario_kinds = tuple(item.kind for item in self.scenarios)
        if len(set(scenario_kinds)) != len(scenario_kinds):
            raise PydanticCustomError(
                "decision_scenarios_duplicate_kind",
                "research scenario kinds must be unique",
            )
        if set(scenario_kinds) != set(ResearchScenarioKind):
            raise PydanticCustomError(
                "decision_scenarios_incomplete_set",
                "research decision requires base, bull, and bear scenarios",
            )
        return self


def _field_value(value: Any, field: str) -> Any:
    if isinstance(value, BaseModel):
        return getattr(value, field, None)
    if isinstance(value, dict):
        return value.get(field)
    return None


ResearchArtifactContent = (
    AnalystReport
    | DecisionBrief
    | ResearchCase
    | DebateAgenda
    | RebuttalReview
    | JudgeDraft
    | RiskReview
    | ResearchDecision
)


class ArtifactGenerationObservation(FrozenModel):
    """One structured-generation path used to produce an artifact component."""

    node: str = Field(min_length=1, max_length=160)
    task_kind: Literal["semantic_structured", "schema_serialization"]
    client_role: Literal[
        "quick_reasoning",
        "deep_reasoning",
        "quick_serializer",
        "deep_serializer",
    ]
    generation_method: ArtifactGenerationMethod


def _artifact_content_type(content: ResearchArtifactContent) -> str:
    if isinstance(content, AnalystReport):
        return "analyst_report"
    if isinstance(content, DecisionBrief):
        return "decision_brief"
    if isinstance(content, ResearchCase):
        return "research_case"
    if isinstance(content, DebateAgenda):
        return "debate_agenda"
    if isinstance(content, RebuttalReview):
        return "rebuttal_review"
    if isinstance(content, JudgeDraft):
        return "judge_draft"
    if isinstance(content, RiskReview):
        return "risk_review"
    if isinstance(content, ResearchDecision):
        return "research_decision"
    raise TypeError(f"unsupported research artifact: {type(content)!r}")


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
    schema_version: Literal["2"] = "2"
    prompt_version: str = Field(
        default="research-v1",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    generation_method: ArtifactGenerationMethod
    generation_observations: tuple[ArtifactGenerationObservation, ...] = ()
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
    schema_version: Literal["2"] = "2"
    prompt_version: str = Field(
        default="research-v1",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    generation_method: ArtifactGenerationMethod
    generation_observations: tuple[ArtifactGenerationObservation, ...] = ()
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
    cache_hit_input_tokens: int = Field(default=0, ge=0)
    cache_miss_input_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    detailed_usage_calls: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)


class RunMetrics(FrozenModel):
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_hit_input_tokens: int = Field(default=0, ge=0)
    cache_miss_input_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    detailed_usage_calls: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    node_metrics: dict[str, NodeMetrics] = Field(default_factory=dict)


class AnalysisRequest(FrozenModel):
    ticker: str = Field(min_length=1, max_length=64)
    analysis_date: date
    asset_type: AssetType | None = None
    profile: RunProfile = RunProfile.STANDARD
    analysts: tuple[Literal["market", "social", "news", "fundamentals"], ...] = (
        "market",
        "social",
        "news",
        "fundamentals",
    )
    llm_provider: str | None = None
    quick_model: str | None = None
    deep_model: str | None = None
    quick_reasoning_effort: str | None = None
    deep_reasoning_effort: str | None = None
    research_kind: Literal["full", "incremental"] = "full"
    full_baseline_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
    )
    # The first Full Cycle is selected automatically.  Once a Timeline exists,
    # callers must make this choice explicitly rather than relying on order.
    make_primary: bool | None = None
    # Keep the union inline so Pydantic preserves the existing OpenAPI shape;
    # a named PEP 695 alias is emitted as a separate schema component.
    output_language: ReportLanguage | str | None = None

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
    def validate_asset_type(self) -> AnalysisRequest:
        if unsupported_crypto_base(self.ticker):
            raise ValueError("Crypto instruments are not supported")
        if not is_supported_equity_symbol(self.ticker):
            raise ValueError("Only listed equity instruments are supported")
        if self.asset_type is None:
            object.__setattr__(self, "asset_type", AssetType.STOCK)
        if self.research_kind == "full" and self.full_baseline_run_id is not None:
            raise ValueError("Full Research must not carry a Full Baseline")
        if self.research_kind == "incremental" and self.full_baseline_run_id is None:
            raise ValueError("Incremental Research requires exactly one full_baseline_run_id")
        return self


class RunRequestSnapshot(FrozenModel):
    """Tolerant request data retained with a Run for history inspection.

    This is deliberately separate from :class:`AnalysisRequest`.  The latter
    is the admission contract for creating research, while this snapshot must
    remain able to represent request values that were accepted by an older
    application version (including ``asset_type='crypto'``).  Snapshot
    validation does not normalize symbols, infer an asset type, or otherwise
    rewrite persisted request data.
    """

    ticker: str = Field(min_length=1, max_length=64)
    analysis_date: date
    asset_type: str | None = None
    profile: RunProfile = RunProfile.STANDARD
    analysts: tuple[Literal["market", "social", "news", "fundamentals"], ...] = (
        "market",
        "social",
        "news",
        "fundamentals",
    )
    llm_provider: str | None = None
    quick_model: str | None = None
    deep_model: str | None = None
    quick_reasoning_effort: str | None = None
    deep_reasoning_effort: str | None = None
    research_kind: Literal["full", "incremental"] = "full"
    full_baseline_run_id: str | None = None
    make_primary: bool | None = None
    output_language: ReportLanguage | str | None = None

    def to_analysis_request(self) -> AnalysisRequest:
        """Cross the creation boundary explicitly when execution is requested."""

        return AnalysisRequest.model_validate(self.model_dump(mode="python"))


class RunEvent(FrozenModel):
    run_id: str
    sequence: int = Field(ge=1)
    attempt: int = Field(ge=1)
    event_type: str
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class StructuredRecoveryNotice(FrozenModel):
    """One successful bounded structured-output recovery rebuilt from events."""

    attempt: int = Field(ge=1)
    node: str = Field(min_length=1, max_length=160)
    initial_reason_code: str = Field(pattern=r"^[a-z0-9_.-]+$")
    recovery_method: ArtifactGenerationMethod
    validation_issue_codes: tuple[str, ...] = ()
    retry_count: int = Field(ge=1)
    recovered_at: datetime

    @field_validator("validation_issue_codes")
    @classmethod
    def validate_issue_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        issues = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"[a-z0-9_.-]+", item) for item in issues):
            raise ValueError("recovery issues must use stable codes")
        return issues


class AnalysisResult(FrozenModel):
    run_id: str
    status: RunStatus
    instrument: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    reports: dict[str, AnalystReport | str]
    decision: ResearchDecision | None
    numeric_audit: DecisionNumericAuditAppendix | None = None
    evidence: EvidenceBundle | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    recoveries: tuple[StructuredRecoveryNotice, ...] = ()
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


class EvidenceSealView(FrozenModel):
    """Durable status of the immutable evidence boundary for one run."""

    status: Literal["pending", "sealed"]
    digest: str | None = None
    item_count: int = Field(default=0, ge=0)
    table_count: int = Field(default=0, ge=0)
    sealed_attempt: int | None = Field(default=None, ge=1)
    sealed_at: datetime | None = None


class RunView(FrozenModel):
    id: str
    source_run_id: str | None = None
    is_research_node: bool = False
    research_schema_version: str | None = None
    information_cutoff_at: datetime | None = None
    method_snapshot: dict[str, Any] | None = None
    research_kind: Literal["full", "incremental"] | None = None
    full_baseline_run_id: str | None = None
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    status: RunStatus
    # Keep the creation schema referenced in OpenAPI for existing clients,
    # while repository hydration and all normal responses use the tolerant
    # snapshot branch below.
    request: RunRequestSnapshot | AnalysisRequest
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

    @field_validator("request", mode="before")
    @classmethod
    def coerce_creation_request(
        cls,
        value: RunRequestSnapshot | AnalysisRequest | Any,
    ) -> RunRequestSnapshot | AnalysisRequest | Any:
        if isinstance(value, AnalysisRequest):
            return value.model_dump(mode="python")
        return value


CURRENT_RESEARCH_SCHEMA_VERSION = "1"


class ResearchNodeView(FrozenModel):
    """A Run-backed Node; it deliberately owns no duplicate research data."""

    id: str
    cycle_id: str
    instrument: str
    analysis_date: date
    research_schema_version: str
    information_cutoff_at: datetime
    method_snapshot: dict[str, Any]
    research_kind: Literal["full", "incremental"]
    full_baseline_run_id: str | None = None
    is_baseline_compatible: bool
    is_cycle_head: bool
    is_primary: bool
    is_active: bool
    trashed_at: datetime | None = None
    trash_cascade_full_run_id: str | None = None
    collection_summary: CollectionSummary | None = None
    research_availability: ResearchAvailability | None = None
    information_advancement: InformationAdvancement | None = None
    performance: PerformanceObservation | None = None
    reassessment: ResearchReassessment | None = None
    decision: ResearchDecision | None = None
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()
    cycle_warning: bool = False


class RunLifecycleImpact(FrozenModel):
    """The exact Run and Cycle scope affected by one lifecycle request."""

    requested_run_id: str
    cycle_id: str | None = None
    research_kind: Literal["full", "incremental"] | None = None
    affected_run_ids: tuple[str, ...] = ()
    cascade_moved_run_ids: tuple[str, ...] = ()
    replacement_primary_cycle_id: str | None = None


class RunLifecycleResult(FrozenModel):
    runs: tuple[RunView, ...] = ()
    changed: int = Field(ge=0)
    impacts: tuple[RunLifecycleImpact, ...] = ()


class PrimaryCycleCandidate(FrozenModel):
    """One active Full Cycle eligible for explicit Primary selection."""

    id: str
    analysis_date: date


class ResearchTimeline(FrozenModel):
    instrument: str
    primary_cycle_id: str | None = None
    active_full_cycles: tuple[PrimaryCycleCandidate, ...] = ()
    nodes: tuple[ResearchNodeView, ...] = ()
    node_total: int = Field(default=0, ge=0)
    node_limit: int = Field(default=50, ge=1, le=200)
    node_offset: int = Field(default=0, ge=0)
    timeline_warning: bool = False


class ResearchTimelineSummary(FrozenModel):
    """Derived Timeline identity and stable, non-duplicated summary metadata."""

    instrument: str
    primary_cycle_id: str | None = None
    node_count: int = Field(ge=1)


class ResearchTimelinePage(FrozenModel):
    items: tuple[ResearchTimelineSummary, ...] = ()
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class ResearchNodeLifecycleState(_StableStrEnum):
    """Expected retained lifecycle state for an explicit comparison selection."""

    ACTIVE = "active"
    TRASHED = "trashed"


class ResearchNodeComparisonSelection(FrozenModel):
    """One explicitly selected side of an on-demand Node Comparison."""

    node_id: str = Field(min_length=1, max_length=36)
    lifecycle_state: ResearchNodeLifecycleState = ResearchNodeLifecycleState.ACTIVE

    @field_validator("node_id", mode="before")
    @classmethod
    def normalize_node_id(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ComparisonValueState(_StableStrEnum):
    """Presentation-safe distinction between schema absence and stored values."""

    RECORDED = "recorded"
    NULL = "null"
    EMPTY = "empty"
    NOT_RECORDED_UNDER_THIS_SCHEMA = "not_recorded_under_this_schema"


class ResearchNodeComparisonValue(FrozenModel):
    state: ComparisonValueState
    value: Any = None


class ResearchNodeDecisionSection(FrozenModel):
    """One fixed Decision section aligned in requested side order."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    values: tuple[ResearchNodeComparisonValue, ...] = Field(min_length=2, max_length=2)


class ResearchNodeComparisonWarning(FrozenModel):
    code: Literal["method_changed"]
    message: str = Field(min_length=1)


class ResearchNodeComparisonSide(FrozenModel):
    """Immutable per-Node context; values are never normalized across sides."""

    node_id: str
    cycle_id: str
    analysis_date: date
    research_schema_version: str
    method_snapshot: dict[str, Any]
    research_kind: Literal["full", "incremental"]
    lifecycle_state: ResearchNodeLifecycleState
    collection_summary: CollectionSummary | None = None
    research_availability: ResearchAvailability | None = None
    information_advancement: InformationAdvancement | None = None
    reassessment: ResearchReassessment | None = None
    decision: dict[str, Any]
    performance: PerformanceObservation | None = None
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()


class ResearchNodeComparison(FrozenModel):
    """Deterministic, read-only comparison of exactly two retained Nodes."""

    instrument: str
    sides: tuple[ResearchNodeComparisonSide, ...] = Field(min_length=2, max_length=2)
    cross_cycle: bool
    method_changed: bool
    warnings: tuple[ResearchNodeComparisonWarning, ...] = ()
    decision_sections: tuple[ResearchNodeDecisionSection, ...]


class CollectionDiagnostic(FrozenModel):
    """A stable, secret-free collection failure class safe for Run events."""

    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")


class CollectionResultState(_StableStrEnum):
    """Truthful result state for one enabled Incremental research domain."""

    DATA = "data"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CollectionTemporalBasis(_StableStrEnum):
    """Temporal basis actually represented by admitted domain observations."""

    PIT = "pit"
    NEAR_LIVE_ADVISORY = "near_live_advisory"


class CollectionSourceProvenance(FrozenModel):
    """One actual source used or attempted for a domain result."""

    source: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    fallback: bool = False
    retrieved_at: datetime
    diagnostic: CollectionDiagnostic | None = None

    @model_validator(mode="after")
    def validate_retrieval_time(self) -> CollectionSourceProvenance:
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("source retrieved_at must include a timezone")
        return self


class CollectionDomainResult(FrozenModel):
    """One actual domain result without a configured-provider attempt ledger."""

    domain: Literal["fundamentals", "market", "news", "social"]
    state: CollectionResultState
    sources: tuple[CollectionSourceProvenance, ...] = ()
    observed_from: datetime | None = None
    observed_through: datetime | None = None
    temporal_bases: tuple[CollectionTemporalBasis, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    diagnostic: CollectionDiagnostic | None = None
    omitted_by_temporal_boundary: bool = False

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_truthful_result(self) -> CollectionDomainResult:
        for field_name in ("observed_from", "observed_through"):
            value = getattr(self, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must include a timezone")
        observed = (self.observed_from, self.observed_through)
        if any(value is not None for value in observed) and any(
            value is None for value in observed
        ):
            raise ValueError("observed window must be complete")
        if (
            self.observed_from is not None
            and self.observed_through is not None
            and self.observed_from > self.observed_through
        ):
            raise ValueError("observed window must be ordered")
        if self.state in {CollectionResultState.DATA, CollectionResultState.PARTIAL}:
            if not self.evidence_refs or not self.temporal_bases:
                raise ValueError("data and partial results require Evidence and a temporal basis")
        elif self.evidence_refs or self.temporal_bases:
            raise ValueError(
                "empty and unavailable results cannot report Evidence or a temporal basis"
            )
        if self.state is CollectionResultState.UNAVAILABLE:
            if self.diagnostic is None:
                raise ValueError("unavailable results require a sanitized diagnostic")
        elif self.state is CollectionResultState.PARTIAL and self.diagnostic is None:
            raise ValueError("partial results require a sanitized limitation")
        elif not self.sources:
            raise ValueError("queried collection results require actual sources")
        source_names = tuple(source.source for source in self.sources)
        if len(source_names) != len(set(source_names)):
            raise ValueError("collection result sources must be unique")
        if len(self.temporal_bases) != len(set(self.temporal_bases)):
            raise ValueError("collection temporal bases must be unique")
        return self


class CollectionSummary(FrozenModel):
    """Actual Incremental collection results and disclosed limitations."""

    version: str = Field(pattern=r"^[0-9]+$")
    market: Literal["united_states", "japan", "mainland_china"]
    domains: tuple[CollectionDomainResult, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_domains(self) -> CollectionSummary:
        domains = tuple(item.domain for item in self.domains)
        if len(domains) != len(set(domains)):
            raise ValueError("collection summary domains must be unique")
        return self


class ResearchAvailabilityStatus(_StableStrEnum):
    AVAILABLE = "available"
    LIMITED = "limited"
    MISSING = "missing"


class ResearchAvailabilityDomain(FrozenModel):
    domain: Literal["fundamentals", "market", "news", "social"]
    status: ResearchAvailabilityStatus


class ResearchAvailability(FrozenModel):
    """Descriptive breadth of actual inputs, without source certification."""

    version: str = Field(pattern=r"^[0-9]+$")
    domains: tuple[ResearchAvailabilityDomain, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_domains(self) -> ResearchAvailability:
        domains = tuple(item.domain for item in self.domains)
        if len(domains) != len(set(domains)):
            raise ValueError("research availability domains must be unique")
        return self


class IncrementalCollectionRequest(FrozenModel):
    """Frozen common request passed to one configured market collection seam."""

    version: str = Field(pattern=r"^[0-9]+$")
    instrument: str = Field(min_length=1)
    market: Literal["united_states", "japan", "mainland_china"]
    route_suffix: str
    baseline_analysis_cutoff: date
    analysis_cutoff: date
    window_start: datetime
    window_end: datetime
    enabled_domains: tuple[Literal["fundamentals", "market", "news", "social"], ...] = Field(
        min_length=1
    )
    configured_routes: Mapping[str, Any]
    near_live_max_age_days: int = Field(default=5, ge=0, le=5)

    @field_validator("configured_routes", mode="after")
    @classmethod
    def freeze_configured_routes(
        cls,
        value: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return _deeply_frozen_mapping(value)

    @model_validator(mode="after")
    def validate_collection_boundary(self) -> IncrementalCollectionRequest:
        if self.baseline_analysis_cutoff >= self.analysis_cutoff:
            raise ValueError("Incremental analysis cutoff must follow the Full Baseline")
        if self.window_start >= self.window_end:
            raise ValueError("Incremental collection window must advance")
        for field_name in ("window_start", "window_end"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        if len(self.enabled_domains) != len(set(self.enabled_domains)):
            raise ValueError("enabled collection domains must be unique")
        return self


class IncrementalEvidenceCandidate(FrozenModel):
    """One collector-produced record before its PIT availability is admitted.

    A source may establish a precise publication instant or only a market-local
    publication date.  The latter is deliberately resolved by the application
    at conservative day-end before it reaches a sealed EvidenceBundle.
    """

    evidence: EvidenceItem
    available_on: date | None = None

    @model_validator(mode="after")
    def validate_availability_shape(self) -> IncrementalEvidenceCandidate:
        if self.evidence.available_at is not None and self.available_on is not None:
            raise ValueError("Evidence availability must use either an instant or a date")
        return self


class IncrementalEvidenceBinding(FrozenModel):
    """Resolution from a collector-owned ref to the admitted sealed ref."""

    candidate_ref: str = Field(pattern=r"^ev_[a-f0-9]{12}$")
    admitted_ref: str | None = Field(default=None, pattern=r"^ev_[a-f0-9]{12}$")


class InformationAdvancement(FrozenModel):
    """Deterministic answer to whether collection can justify an Incremental Node."""

    advanced: bool
    reasons: tuple[
        Literal[
            "admissible_observation",
            "completed_stock_session",
        ],
        ...,
    ] = ()
    observation_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_advancement_inputs(self) -> InformationAdvancement:
        if self.advanced != bool(self.reasons):
            raise ValueError("information advancement must agree with its reasons")
        has_observations = bool(self.observation_ids)
        if has_observations != ("admissible_observation" in self.reasons):
            raise ValueError("new observation identities must agree with advancement reasons")
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("new observation identities must be unique")
        return self


class IncrementalCollectionPreflight(FrozenModel):
    """Simplified deterministic gate result, safe to persist in sanitized events."""

    collection_summary: CollectionSummary
    research_availability: ResearchAvailability
    information_advancement: InformationAdvancement
    diagnostics: tuple[CollectionDiagnostic, ...] = ()


class ReassessmentDisposition(_StableStrEnum):
    REAFFIRMED = "reaffirmed"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    OVERTURNED = "overturned"
    UNRESOLVED = "unresolved"


class ResearchReassessmentEntry(FrozenModel):
    component_id: str = Field(pattern=_DECISION_COMPONENT_PATH_PATTERN.pattern)
    disposition: ReassessmentDisposition
    reason: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class ResearchReassessment(FrozenModel):
    entries: tuple[ResearchReassessmentEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_components(self) -> ResearchReassessment:
        if len({entry.component_id for entry in self.entries}) != len(self.entries):
            raise ValueError("reassessment components must be unique")
        return self


class MarketSeriesPoint(FrozenModel):
    session: date
    completed_at: datetime
    adjusted_close: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_completed_at(self) -> MarketSeriesPoint:
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("market-series completed_at must include a timezone")
        return self


class MarketSeriesResult(FrozenModel):
    """One completed-session series from a single provider and retrieval vintage."""

    instrument: str = Field(min_length=1)
    source: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    adjustment_basis: str = Field(min_length=1, max_length=120)
    retrieved_at: datetime
    fallback: bool = False
    points: tuple[MarketSeriesPoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_series(self) -> MarketSeriesResult:
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("market-series retrieved_at must include a timezone")
        sessions = tuple(point.session for point in self.points)
        if sessions != tuple(sorted(set(sessions))):
            raise ValueError("market-series sessions must be unique and ordered")
        completions = tuple(point.completed_at for point in self.points)
        if completions != tuple(sorted(completions)):
            raise ValueError("market-series completion times must be ordered")
        if any(completed_at > self.retrieved_at for completed_at in completions):
            raise ValueError("market-series sessions must complete before retrieval")
        return self


class BenchmarkSeriesResult(FrozenModel):
    """One benchmark's collected series or truthful unavailability."""

    name: str = Field(min_length=1, max_length=120)
    series: MarketSeriesResult | None = None
    unavailable_diagnostic: CollectionDiagnostic | None = None

    @model_validator(mode="after")
    def validate_result_state(self) -> BenchmarkSeriesResult:
        if (self.series is None) == (self.unavailable_diagnostic is None):
            raise ValueError(
                "benchmark collection requires either a series or an unavailable diagnostic"
            )
        return self


class IncrementalCollectionResult(FrozenModel):
    """Deterministic collection observations plus unsealed new Evidence."""

    collection_summary: CollectionSummary
    evidence: tuple[IncrementalEvidenceCandidate, ...] = ()
    stock_series: MarketSeriesResult | None = None
    stock_series_evidence_ref: str | None = Field(
        default=None,
        pattern=r"^ev_[a-f0-9]{12}$",
    )
    benchmark_series: tuple[BenchmarkSeriesResult, ...] = ()

    @model_validator(mode="after")
    def validate_collection_links(self) -> IncrementalCollectionResult:
        if self.stock_series is None and self.stock_series_evidence_ref is not None:
            raise ValueError("stock-series Evidence link requires a stock series")
        names = tuple(item.name for item in self.benchmark_series)
        if len(names) != len(set(names)):
            raise ValueError("benchmark series names must be unique")
        return self


class PerformanceComponentStatus(_StableStrEnum):
    CALCULATED = "calculated"
    NOT_YET_OBSERVABLE = "not_yet_observable"
    UNAVAILABLE = "unavailable"


class PerformanceCalculationRecord(FrozenModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    fallback: bool = False
    adjustment_basis: str = Field(min_length=1, max_length=120)
    retrieved_at: datetime
    baseline_information_cutoff_at: datetime
    target_information_cutoff_at: datetime
    start_session: date
    end_session: date
    start_value: float = Field(gt=0, allow_inf_nan=False)
    end_value: float = Field(gt=0, allow_inf_nan=False)
    formula: Literal["(end_value / start_value) - 1"] = "(end_value / start_value) - 1"
    unrounded_return: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_calculation_boundary(self) -> PerformanceCalculationRecord:
        for field_name in (
            "retrieved_at",
            "baseline_information_cutoff_at",
            "target_information_cutoff_at",
        ):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        if self.baseline_information_cutoff_at >= self.target_information_cutoff_at:
            raise ValueError("Performance information cutoffs must advance")
        if self.start_session >= self.end_session:
            raise ValueError("calculated Performance sessions must advance")
        expected_return = (self.end_value / self.start_value) - 1
        if not math.isclose(
            self.unrounded_return,
            expected_return,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise ValueError("unrounded return must match endpoint values")
        return self


class PerformanceComponent(FrozenModel):
    status: PerformanceComponentStatus
    reason: str | None = Field(default=None, min_length=1)
    calculation: PerformanceCalculationRecord | None = None

    @model_validator(mode="after")
    def validate_component_state(self) -> PerformanceComponent:
        if self.status is PerformanceComponentStatus.CALCULATED:
            if self.calculation is None:
                raise ValueError("calculated Performance requires a calculation record")
        elif self.calculation is not None or self.reason is None:
            raise ValueError(
                "non-calculated Performance requires a reason and no calculation record"
            )
        return self


class BenchmarkContext(FrozenModel):
    name: str = Field(min_length=1, max_length=120)
    component: PerformanceComponent


class PerformanceObservation(FrozenModel):
    stock: PerformanceComponent
    benchmarks: tuple[BenchmarkContext, ...] = ()

    @model_validator(mode="after")
    def validate_unique_benchmarks(self) -> PerformanceObservation:
        names = tuple(item.name for item in self.benchmarks)
        if len(names) != len(set(names)):
            raise ValueError("Benchmark Context names must be unique")
        return self


class FullResearchRequiredReason(FrozenModel):
    code: Literal[
        "thesis.material_reversal",
        "identity.uncertain",
        "attribution.unreliable",
        "evidence.material_conflict",
    ]
    message: str = Field(min_length=1)
    origin: Literal["deterministic", "semantic"]
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class IncrementalSynthesisInput(FrozenModel):
    full_baseline_run_id: str = Field(min_length=1, max_length=36)
    full_baseline_decision: ResearchDecision
    permitted_baseline_evidence_refs: tuple[str, ...] = ()
    incremental_evidence: EvidenceBundle
    collection_summary: CollectionSummary
    research_availability: ResearchAvailability
    information_advancement: InformationAdvancement
    performance: PerformanceObservation
    method_snapshot: dict[str, Any]


class IncrementalSynthesis(FrozenModel):
    reassessment: ResearchReassessment
    decision: ResearchDecision
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()


class IncrementalNodeProducts(FrozenModel):
    collection_summary: CollectionSummary
    research_availability: ResearchAvailability
    information_advancement: InformationAdvancement
    performance: PerformanceObservation
    reassessment: ResearchReassessment
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()


class RunAttemptView(FrozenModel):
    """Observed execution usage and lifecycle for one retry attempt."""

    attempt: int = Field(ge=1)
    status: RunStatus
    resume_count: int = Field(default=0, ge=0)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None


class RunSummaryView(RunView):
    research_rating: ResearchRating | None = None


class RunPage(FrozenModel):
    items: tuple[RunSummaryView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class RecentInstrument(FrozenModel):
    ticker: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    last_used_at: datetime


class RunExport(FrozenModel):
    """Versioned, self-contained durable run export."""

    schema_version: Literal["9"] = "9"
    run: RunView
    result: AnalysisResult
    evidence: EvidenceBundle | None = None
    artifacts: tuple[ResearchArtifact, ...] = ()
    attempts: tuple[RunAttemptView, ...] = ()
