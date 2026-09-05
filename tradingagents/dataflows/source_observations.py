"""Producer-owned observations shared by Full and Incremental research.

The scoped sink transports structured values beside existing string interfaces;
it never parses rendered reports or changes provider routing.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time
from typing import Any

from tradingagents.application.contracts import EvidenceItem, EvidenceOrigin

_sink: ContextVar[list[SourceObservation] | None] = ContextVar("source_observations", default=None)


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
            from .symbol_utils import market_timezone

            if instrument is None:
                raise ValueError("instrument is required for date-only publication evidence")
            available_at = datetime.combine(self.available_on, time.max, tzinfo=market_timezone(instrument))
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


@contextmanager
def capture_observations() -> Iterator[list[SourceObservation]]:
    observations: list[SourceObservation] = []
    token = _sink.set(observations)
    try:
        yield observations
    finally:
        _sink.reset(token)


@contextmanager
def routed_observations(*, fallback: bool) -> Iterator[None]:
    """Publish only the successful route leg, with its actual fallback status."""
    parent = _sink.get()
    if parent is None:
        yield
        return
    with capture_observations() as observations:
        yield
    parent.extend(replace(row, fallback=row.fallback or fallback) for row in observations)


def publish_observation(
    source: str,
    kind: str,
    key: str,
    values: dict,
    *,
    effective_date=None,
    available_on=None,
    available_at=None,
    retrieved_at: datetime | None = None,
    timing: str | None = None,
    fallback: bool = False,
) -> None:
    sink = _sink.get()
    if sink is None:
        return
    sink.append(
        SourceObservation(
            source=source,
            kind=kind,
            key=str(key),
            values=scalar(values),
            effective_date=as_date(effective_date),
            available_on=as_date(available_on),
            available_at=available_at,
            retrieved_at=retrieved_at or datetime.now(UTC),
            timing=timing
            or (
                "publication-date filtered"
                if available_on or available_at
                else "near-live snapshot; publication time unavailable"
            ),
            fallback=fallback,
        )
    )


_FINANCIAL_FIELDS = {
    "income": ("Total Revenue", "Operating Income", "Net Income", "Basic EPS"),
    "balance": (
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Total Debt",
        "Total Assets",
        "Total Liabilities Net Minority Interest",
        "Stockholders Equity",
    ),
    "cashflow": (
        "Operating Cash Flow",
        "Capital Expenditure",
        "Free Cash Flow",
        "Investing Cash Flow",
        "Financing Cash Flow",
        "End Cash Position",
    ),
}


def publish_yahoo_statement(frame, ticker: str, kind: str, freq: str, *, source="yfinance"):
    from tradingagents.dataflows.measurement import instrument_currency

    for period in sorted(frame.columns, reverse=True)[:4]:
        values = {
            label: scalar(frame.loc[label, period])
            for label in _FINANCIAL_FIELDS[kind]
            if label in frame.index
        }
        if not any(value is not None for value in values.values()):
            continue
        values.update(
            currency=instrument_currency(ticker),
            currency_basis="instrument market convention; statement currency unverified",
            unit="currency units; EPS per share",
            frequency=freq,
            period_basis="provider fiscal period; not a filing timestamp",
        )
        publish_observation(
            source,
            f"financial_{kind}",
            f"{ticker}:{as_date(period)}",
            values,
            effective_date=period,
        )
