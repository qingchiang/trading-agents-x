"""Typed producer data, observations and provenance without SDK dependencies."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any, Literal

from tradingagents.domain.evidence import EvidenceItem, EvidenceOrigin

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
    available_on: date | None = None
    effective_date: date | None = None


def scalar(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): scalar(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [scalar(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, float, int, bool)):
        return value
    return str(value)


def as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SourceObservation:
    source: str
    kind: str
    key: str
    values: dict[str, Any]
    retrieved_at: datetime
    effective_date: date | None = None
    available_on: date | None = None
    available_at: datetime | None = None
    timing: str = "near-live snapshot; publication time unavailable"
    fallback: bool = False

    @property
    def is_pit(self) -> bool:
        return self.available_on is not None or self.available_at is not None

    @property
    def content(self) -> str:
        return f"{self.kind}: {self.key}\n" + json.dumps(
            self.values,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )

    @property
    def identity(self) -> str:
        identity_values = (
            {key: value for key, value in self.values.items() if key != "display"}
            if self.kind == "macro_indicator"
            else self.values
        )
        payload = [
            self.source,
            self.kind,
            self.key,
            identity_values,
            scalar(self.effective_date),
            scalar(self.available_on),
            None if self.available_on else scalar(self.available_at),
        ]
        return (
            "ob_"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()[:16]
        )

    def dump(self) -> dict:
        return scalar(asdict(self))

    @classmethod
    def load(cls, payload: dict) -> SourceObservation:
        fields = dict(payload)
        for key in ("retrieved_at", "available_at"):
            if fields.get(key):
                fields[key] = datetime.fromisoformat(fields[key])
        for key in ("effective_date", "available_on"):
            fields[key] = as_date(fields.get(key))
        return cls(**fields)

    def evidence(self, requested_date: date, *, instrument: str | None = None) -> EvidenceItem:
        available_at = self.available_at
        if available_at is None and self.available_on is not None:
            from tradingagents.domain.instruments import market_timezone

            if instrument is None:
                raise ValueError("instrument is required for date-only publication evidence")
            available_at = datetime.combine(
                self.available_on, time.max, tzinfo=market_timezone(instrument)
            )
        source_id = re.sub(r"[^a-z0-9_.-]+", "_", self.source.casefold()).strip("_")
        return EvidenceItem.create(
            source=source_id,
            evidence_type=self.kind,
            requested_date=requested_date,
            effective_date=self.effective_date,
            available_at=available_at,
            content=self.content,
            fallback=self.fallback,
            origins=(
                EvidenceOrigin(
                    source=source_id,
                    evidence_type=self.kind,
                    fallback=self.fallback,
                    requested=requested_date.isoformat(),
                    effective=str(self.effective_date or "retrieval-time snapshot"),
                    timing=self.timing,
                    retrieved_at=self.retrieved_at.isoformat(),
                    temporal_scope="point_in_time" if self.is_pit else "live_only",
                ),
            ),
            provenance={"observation_identity": self.identity, "observation": self.dump()},
        )
