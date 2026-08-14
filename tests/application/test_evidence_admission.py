"""Deterministic PIT and Near-live Evidence admission policy."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.application.evidence_admission import (
    EvidenceAdmissionReason,
    evaluate_evidence_admission,
)

TOKYO = ZoneInfo("Asia/Tokyo")


@pytest.mark.parametrize("age_days", [0, 5])
def test_near_live_admits_inclusive_market_local_window(age_days: int) -> None:
    cutoff = date(2026, 8, 10)
    retrieved_at = datetime(2026, 8, 10 + age_days, 21, 0, tzinfo=TOKYO)

    decision = evaluate_evidence_admission(
        temporal_scope="live_only",
        analysis_date=cutoff,
        instrument="4568.T",
        retrieved_at=retrieved_at,
        sealed_at=datetime(2026, 8, 15, 23, 0, tzinfo=TOKYO),
    )

    assert decision.admitted is True
    assert decision.advisory is True
    assert decision.reason is EvidenceAdmissionReason.NEAR_LIVE_ADVISORY


@pytest.mark.parametrize(
    ("retrieved_at", "expected_reason"),
    [
        (
            datetime(2026, 8, 16, 0, 0, tzinfo=TOKYO),
            EvidenceAdmissionReason.NEAR_LIVE_WINDOW_EXCEEDED,
        ),
        (
            datetime(2026, 8, 9, 23, 59, tzinfo=TOKYO),
            EvidenceAdmissionReason.CUTOFF_AFTER_RETRIEVAL,
        ),
    ],
)
def test_near_live_rejects_age_six_and_future_cutoffs(
    retrieved_at: datetime,
    expected_reason: EvidenceAdmissionReason,
) -> None:
    decision = evaluate_evidence_admission(
        temporal_scope="live_only",
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        retrieved_at=retrieved_at,
        sealed_at=datetime(2026, 8, 16, 1, 0, tzinfo=TOKYO),
    )

    assert decision.admitted is False
    assert decision.reason is expected_reason


@pytest.mark.parametrize(
    ("retrieved_at", "expected_reason"),
    [
        (None, EvidenceAdmissionReason.RETRIEVED_AT_MISSING),
        (
            datetime(2026, 8, 14, 12, 0),
            EvidenceAdmissionReason.RETRIEVED_AT_NAIVE,
        ),
    ],
)
def test_near_live_requires_auditable_retrieval_time(
    retrieved_at: datetime | None,
    expected_reason: EvidenceAdmissionReason,
) -> None:
    decision = evaluate_evidence_admission(
        temporal_scope="live_only",
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        retrieved_at=retrieved_at,
        sealed_at=datetime(2026, 8, 14, 13, 0, tzinfo=TOKYO),
    )

    assert decision.admitted is False
    assert decision.reason is expected_reason


def test_near_live_rejects_retrieval_later_than_seal() -> None:
    decision = evaluate_evidence_admission(
        temporal_scope="live_only",
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        retrieved_at=datetime(2026, 8, 14, 13, 1, tzinfo=TOKYO),
        sealed_at=datetime(2026, 8, 14, 13, 0, tzinfo=TOKYO),
    )

    assert decision.admitted is False
    assert decision.reason is EvidenceAdmissionReason.RETRIEVED_AFTER_SEAL


def test_future_effective_date_is_rejected_for_pit_and_near_live() -> None:
    for temporal_scope in ("point_in_time", "live_only"):
        decision = evaluate_evidence_admission(
            temporal_scope=temporal_scope,
            analysis_date=date(2026, 8, 10),
            instrument="4568.T",
            retrieved_at=datetime(2026, 8, 14, 13, 0, tzinfo=TOKYO),
            sealed_at=datetime(2026, 8, 14, 13, 0, tzinfo=TOKYO),
            effective_dates=(date(2026, 8, 11),),
        )

        assert decision.admitted is False
        assert decision.reason is EvidenceAdmissionReason.EFFECTIVE_AFTER_CUTOFF


def test_near_live_exception_does_not_relax_pit_frontier() -> None:
    frontier = datetime(2026, 8, 10, 17, 0, tzinfo=TOKYO)
    decision = evaluate_evidence_admission(
        temporal_scope="point_in_time",
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        available_at=datetime(2026, 8, 10, 18, 0, tzinfo=TOKYO),
        information_frontier=frontier,
    )

    assert decision.admitted is False
    assert decision.advisory is False
    assert decision.reason is EvidenceAdmissionReason.AFTER_INFORMATION_FRONTIER


def test_unknown_scope_fails_closed() -> None:
    decision = evaluate_evidence_admission(
        temporal_scope="unknown",
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
    )

    assert decision.admitted is False
    assert decision.reason is EvidenceAdmissionReason.UNKNOWN_TEMPORAL_SCOPE
