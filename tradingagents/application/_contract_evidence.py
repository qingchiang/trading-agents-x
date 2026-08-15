"""Evidence item, table, and sealed-bundle application contracts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from tradingagents.application.evidence_admission import (
    EvidenceAdmissionReason,
    evaluate_evidence_admission,
)
from tradingagents.dataflows.symbol_utils import market_timezone

from ._contract_base import FrozenModel, _unique_evidence_refs, utc_now


class MarketReferenceBasis(StrEnum):
    OBSERVED = "observed"
    INTERPRETED = "interpreted"
    DERIVED = "derived"


class EvidenceQuality(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


class EvidenceTemporalScope(StrEnum):
    """Whether source content is valid at the cutoff or only at retrieval time."""

    POINT_IN_TIME = "point_in_time"
    LIVE_ONLY = "live_only"
    UNKNOWN = "unknown"


class TableDataType(StrEnum):
    """Machine-readable type of values in one deterministic evidence column."""

    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    PERCENT = "percent"
    CURRENCY = "currency"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"


class MeasurementKind(StrEnum):
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
    information_frontier: datetime | None = None
    items: tuple[EvidenceItem, ...]
    tables: tuple[EvidenceTable, ...] = ()
    sealed_at: datetime = Field(default_factory=utc_now)
    digest: str | None = None

    @model_validator(mode="after")
    def validate_bundle(self) -> EvidenceBundle:
        if (
            self.information_frontier is not None
            and self.information_frontier.utcoffset() is None
        ):
            raise ValueError("information_frontier must include a timezone")
        if self.sealed_at.utcoffset() is None:
            raise ValueError("sealed_at must include a timezone")
        if self.digest is None:
            admitted_items = tuple(
                item
                for item in (
                    _apply_bundle_admission(
                        item,
                        instrument=self.instrument,
                        analysis_date=self.analysis_date,
                        sealed_at=self.sealed_at,
                        information_frontier=self.information_frontier,
                    )
                    for item in self.items
                )
                if item is not None
            )
            content_refs = {
                item.ref
                for item in admitted_items
                if item.provenance.get("evidence_admission", {}).get("status")
                != "withheld"
            }
            object.__setattr__(self, "items", admitted_items)
            object.__setattr__(
                self,
                "tables",
                tuple(
                    table
                    for table in self.tables
                    if set(table.evidence_refs).issubset(content_refs)
                ),
            )
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
        calculated = _evidence_bundle_digest(
            _context_bound_evidence_bundle_payload(self)
        )
        if self.digest is None:
            object.__setattr__(self, "digest", calculated)
        else:
            legacy_calculated = _evidence_bundle_digest(
                _legacy_evidence_bundle_payload(self)
            )
            if self.digest not in {calculated, legacy_calculated}:
                raise ValueError(
                    "evidence bundle digest does not match its admission context"
                )
            _audit_persisted_bundle_admission(self)
        return self


def _context_bound_evidence_bundle_payload(bundle: EvidenceBundle) -> dict[str, Any]:
    return {
        "version": bundle.version,
        "instrument": bundle.instrument,
        "analysis_date": bundle.analysis_date.isoformat(),
        "information_frontier": (
            bundle.information_frontier.isoformat()
            if bundle.information_frontier is not None
            else None
        ),
        "sealed_at": bundle.sealed_at.isoformat(),
        "items": [item.model_dump(mode="json") for item in bundle.items],
        "tables": [table.model_dump(mode="json") for table in bundle.tables],
    }


def _legacy_evidence_bundle_payload(bundle: EvidenceBundle) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "items": [item.model_dump(mode="json") for item in bundle.items],
        "tables": [table.model_dump(mode="json") for table in bundle.tables],
    }
    if bundle.information_frontier is not None:
        payload["information_frontier"] = bundle.information_frontier.isoformat()
    return payload


def _evidence_bundle_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _audit_persisted_bundle_admission(bundle: EvidenceBundle) -> None:
    safe_content_refs: set[str] = set()
    for item in bundle.items:
        admitted = _apply_bundle_admission(
            item,
            instrument=bundle.instrument,
            analysis_date=bundle.analysis_date,
            sealed_at=bundle.sealed_at,
            information_frontier=bundle.information_frontier,
        )
        visible_payload = item.content is not None or item.value is not None
        admitted_payload_is_unchanged = (
            admitted is not None
            and admitted.content == item.content
            and admitted.value == item.value
            and admitted.provenance.get("evidence_admission", {}).get("status")
            != "withheld"
        )
        if visible_payload and not admitted_payload_is_unchanged:
            raise ValueError(
                f"{item.ref} contains content outside its admission boundary"
            )
        if admitted_payload_is_unchanged:
            safe_content_refs.add(item.ref)

    for table in bundle.tables:
        if not set(table.evidence_refs).issubset(safe_content_refs):
            raise ValueError(
                f"{table.id} contains data outside its admission boundary"
            )


def _apply_bundle_admission(
    item: EvidenceItem,
    *,
    instrument: str,
    analysis_date: date,
    sealed_at: datetime,
    information_frontier: datetime | None,
) -> EvidenceItem | None:
    """Apply temporal admission without changing the persisted schema."""

    scopes = {origin.temporal_scope for origin in item.origins}
    if not scopes:
        scopes = {EvidenceTemporalScope.POINT_IN_TIME}
    if scopes == {EvidenceTemporalScope.POINT_IN_TIME}:
        decision = evaluate_evidence_admission(
            temporal_scope=EvidenceTemporalScope.POINT_IN_TIME.value,
            analysis_date=analysis_date,
            instrument=instrument,
            effective_dates=tuple(
                value
                for value in (
                    item.effective_date,
                    *(origin.effective_date for origin in item.origins),
                )
                if value is not None
            ),
            available_at=item.available_at,
            information_frontier=information_frontier,
        )
        if decision.reason is EvidenceAdmissionReason.AFTER_INFORMATION_FRONTIER or (
            decision.reason is EvidenceAdmissionReason.AVAILABLE_AFTER_CUTOFF
            and information_frontier is not None
        ):
            return None
        if decision.reason is EvidenceAdmissionReason.EFFECTIVE_AFTER_CUTOFF:
            return _mark_item_admission(
                item,
                status="withheld",
                reason=decision.reason,
                content=None,
                value=None,
                quality=EvidenceQuality.UNAVAILABLE,
                effective_date=(
                    item.effective_date
                    if item.effective_date is None
                    or item.effective_date <= analysis_date
                    else None
                ),
            )
        return item

    if scopes == {EvidenceTemporalScope.LIVE_ONLY}:
        decisions = tuple(
            evaluate_evidence_admission(
                temporal_scope=EvidenceTemporalScope.LIVE_ONLY.value,
                analysis_date=analysis_date,
                instrument=instrument,
                retrieved_at=origin.retrieved_at,
                sealed_at=sealed_at,
                effective_dates=tuple(
                    value
                    for value in (item.effective_date, origin.effective_date)
                    if value is not None
                ),
            )
            for origin in item.origins
        )
        failed = next((decision for decision in decisions if not decision.admitted), None)
        if failed is not None:
            return _mark_item_admission(
                item,
                status="withheld",
                reason=failed.reason,
                content=None,
                value=None,
                quality=EvidenceQuality.UNAVAILABLE,
                effective_date=(
                    item.effective_date
                    if item.effective_date is None
                    or item.effective_date <= analysis_date
                    else None
                ),
            )
        return item.model_copy(update={"quality": EvidenceQuality.LOW})

    return _mark_item_admission(
        item,
        status="withheld",
        reason=EvidenceAdmissionReason.UNKNOWN_TEMPORAL_SCOPE,
        content=None,
        value=None,
        quality=EvidenceQuality.UNAVAILABLE,
    )


def _mark_item_admission(
    item: EvidenceItem,
    *,
    status: str,
    reason: EvidenceAdmissionReason,
    **updates: Any,
) -> EvidenceItem:
    provenance = dict(item.provenance)
    provenance["evidence_admission"] = {
        "status": status,
        "reason": reason.value,
    }
    return item.model_copy(update={"provenance": provenance, **updates})
