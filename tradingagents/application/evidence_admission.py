"""Deterministic admission policy for PIT and bounded Near-live Evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from tradingagents.dataflows.lookahead import LIVE_SNAPSHOT_MAX_AGE_DAYS
from tradingagents.dataflows.symbol_utils import market_timezone


class EvidenceAdmissionReason(str, Enum):
    """Stable outcomes emitted by the shared Evidence admission policy."""

    POINT_IN_TIME = "point_in_time"
    NEAR_LIVE_ADVISORY = "near_live_advisory"
    UNKNOWN_TEMPORAL_SCOPE = "unknown_temporal_scope"
    EFFECTIVE_AFTER_CUTOFF = "effective_after_cutoff"
    AVAILABLE_AT_NAIVE = "available_at_naive"
    AVAILABLE_AFTER_CUTOFF = "available_after_cutoff"
    AFTER_INFORMATION_FRONTIER = "after_information_frontier"
    RETRIEVED_AT_MISSING = "retrieved_at_missing"
    RETRIEVED_AT_INVALID = "retrieved_at_invalid"
    RETRIEVED_AT_NAIVE = "retrieved_at_naive"
    SEALED_AT_NAIVE = "sealed_at_naive"
    RETRIEVED_AFTER_SEAL = "retrieved_after_seal"
    CUTOFF_AFTER_RETRIEVAL = "cutoff_after_retrieval"
    NEAR_LIVE_WINDOW_EXCEEDED = "near_live_window_exceeded"


@dataclass(frozen=True)
class EvidenceAdmissionDecision:
    """One auditable policy outcome independent of persistence schemas."""

    admitted: bool
    reason: EvidenceAdmissionReason
    advisory: bool = False


def evaluate_evidence_admission(
    *,
    temporal_scope: str,
    analysis_date: date,
    instrument: str,
    retrieved_at: str | datetime | None = None,
    sealed_at: datetime | None = None,
    effective_dates: Iterable[date] = (),
    available_at: datetime | None = None,
    information_frontier: datetime | None = None,
) -> EvidenceAdmissionDecision:
    """Evaluate one source/span against PIT or bounded Near-live semantics.

    Near-live age is derived from the producer-owned retrieval timestamp in the
    instrument market. Replay time is deliberately absent from this API.
    """

    if any(value > analysis_date for value in effective_dates):
        return _rejected(EvidenceAdmissionReason.EFFECTIVE_AFTER_CUTOFF)

    if temporal_scope == "point_in_time":
        if available_at is not None:
            if available_at.utcoffset() is None:
                return _rejected(EvidenceAdmissionReason.AVAILABLE_AT_NAIVE)
            if (
                available_at.astimezone(market_timezone(instrument)).date()
                > analysis_date
            ):
                return _rejected(EvidenceAdmissionReason.AVAILABLE_AFTER_CUTOFF)
            if (
                information_frontier is not None
                and available_at > information_frontier
            ):
                return _rejected(
                    EvidenceAdmissionReason.AFTER_INFORMATION_FRONTIER
                )
        return EvidenceAdmissionDecision(
            admitted=True,
            reason=EvidenceAdmissionReason.POINT_IN_TIME,
        )

    if temporal_scope != "live_only":
        return _rejected(EvidenceAdmissionReason.UNKNOWN_TEMPORAL_SCOPE)

    retrieved = _parse_retrieved_at(retrieved_at)
    if retrieved is None:
        reason = (
            EvidenceAdmissionReason.RETRIEVED_AT_MISSING
            if retrieved_at is None
            else EvidenceAdmissionReason.RETRIEVED_AT_INVALID
        )
        return _rejected(reason)
    if retrieved.utcoffset() is None:
        return _rejected(EvidenceAdmissionReason.RETRIEVED_AT_NAIVE)
    if sealed_at is not None:
        if sealed_at.utcoffset() is None:
            return _rejected(EvidenceAdmissionReason.SEALED_AT_NAIVE)
        if retrieved > sealed_at:
            return _rejected(EvidenceAdmissionReason.RETRIEVED_AFTER_SEAL)

    retrieved_date = retrieved.astimezone(market_timezone(instrument)).date()
    age_days = (retrieved_date - analysis_date).days
    if age_days < 0:
        return _rejected(EvidenceAdmissionReason.CUTOFF_AFTER_RETRIEVAL)
    if age_days > LIVE_SNAPSHOT_MAX_AGE_DAYS:
        return _rejected(EvidenceAdmissionReason.NEAR_LIVE_WINDOW_EXCEEDED)
    return EvidenceAdmissionDecision(
        admitted=True,
        reason=EvidenceAdmissionReason.NEAR_LIVE_ADVISORY,
        advisory=True,
    )


def _parse_retrieved_at(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _rejected(reason: EvidenceAdmissionReason) -> EvidenceAdmissionDecision:
    return EvidenceAdmissionDecision(admitted=False, reason=reason)
