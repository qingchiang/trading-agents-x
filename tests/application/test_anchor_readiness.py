from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from tradingagents.application import anchor_readiness
from tradingagents.application.anchor_readiness import (
    AnchorReadinessReason,
    source_record_versions_digest,
    validate_japanese_anchor_readiness,
)
from tradingagents.application.contracts import AnalysisRequest
from tradingagents.application.market_readiness import (
    MarketDataNotReadyError,
    MarketDataReadiness,
)
from tradingagents.application.research import (
    CapabilitySourceContract,
    MarketResearchCapability,
    TransitionContinuityRule,
)
from tradingagents.provenance import (
    SourceObservation,
    SourceWatermark,
    attach_source_observations,
    attach_source_watermarks,
)

FIXTURE = Path(__file__).parent / "fixtures/jp_anchor_readiness_regression.json"
FRONTIER = datetime.fromisoformat("2026-08-10T23:59:59.999999+09:00")


def _market_ready(_symbol: str, cutoff: date) -> MarketDataReadiness:
    return MarketDataReadiness(
        requested_cutoff=cutoff,
        market_effective_date=date(2026, 8, 10),
        observed_bar_date=date(2026, 8, 10),
    )


def _fixture_payload() -> str:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = attach_source_observations(
        "Sanitized deterministic readiness fixture.",
        *(SourceObservation(**item) for item in raw["source_observations"]),
    )
    return attach_source_watermarks(
        payload,
        *(SourceWatermark(**item) for item in raw["source_watermarks"]),
    )


def _check(*, market_checker=_market_ready, news_payload: str | None = None):
    return validate_japanese_anchor_readiness(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-08-10"),
        information_frontier=FRONTIER,
        market_checker=market_checker,
        news_collector=lambda *_args, **_kwargs: news_payload or _fixture_payload(),
    )


def test_regression_fixture_accepts_edinet_grouping_and_tdnet_pre_anchor_truncation() -> None:
    result = _check()

    assert result.ready
    assert result.reasons == ()
    assert result.information_frontier == FRONTIER
    assert {item.source for item in result.source_frontiers} == {
        "EDINET",
        "TDnet",
        "J-Quants adjusted OHLCV",
    }
    tdnet = next(item for item in result.source_frontiers if item.source == "TDnet")
    edinet = next(item for item in result.source_frontiers if item.source == "EDINET")
    assert tdnet.status == "limited"
    assert tdnet.limitations == ("Requested interval was truncated by the TDnet rolling archive.",)
    assert tdnet.limitation_kind == "archive_truncation"
    assert tdnet.returned_records == 0
    assert tdnet.reported_records == 0
    assert tdnet.record_versions_digest == source_record_versions_digest(())
    assert edinet.returned_records == 1
    assert edinet.reported_records == 1
    assert edinet.record_versions_digest == source_record_versions_digest(
        ("edinet-doc-S100",)
    )
    assert result.metrics.llm_calls == 0
    assert result.metrics.tool_calls == 2
    assert result.metrics.cost_usd is None
    assert result.metrics.wall_time_seconds >= 0


def test_missing_market_observation_returns_stable_typed_reason_without_news_call() -> None:
    news_calls = 0

    def not_ready(_symbol: str, _cutoff: date):
        raise MarketDataNotReadyError("bar missing")

    def news(*_args, **_kwargs):
        nonlocal news_calls
        news_calls += 1
        return _fixture_payload()

    result = validate_japanese_anchor_readiness(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-08-10"),
        information_frontier=FRONTIER,
        market_checker=not_ready,
        news_collector=news,
    )

    assert not result.ready
    assert result.reasons == (AnchorReadinessReason.MISSING_MARKET_OBSERVATION,)
    assert result.metrics.llm_calls == 0
    assert result.metrics.tool_calls == 1
    assert news_calls == 0


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda raw: raw["source_watermarks"].__setitem__(
                1, {**raw["source_watermarks"][1], "status": "unavailable"}
            ),
            AnchorReadinessReason.REQUIRED_CAPABILITY_UNAVAILABLE,
        ),
        (
            lambda raw: raw["source_watermarks"].__setitem__(
                0,
                {
                    **raw["source_watermarks"][0],
                    "information_frontier": "2026-08-12T09:00:00+09:00",
                },
            ),
            AnchorReadinessReason.UNSAFE_POINT_IN_TIME_BOUNDARY,
        ),
        (
            lambda raw: raw["source_observations"][0].update(
                {"replaces_version_id": "edinet-doc-missing"}
            ),
            AnchorReadinessReason.INVALID_SOURCE_CLOSURE,
        ),
    ],
)
def test_source_failures_return_stable_typed_reasons(mutate, reason) -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    mutate(raw)
    payload = attach_source_observations(
        "Sanitized deterministic readiness fixture.",
        *(SourceObservation(**item) for item in raw["source_observations"]),
    )
    payload = attach_source_watermarks(
        payload,
        *(SourceWatermark(**item) for item in raw["source_watermarks"]),
    )

    result = _check(news_payload=payload)

    assert not result.ready
    assert reason in result.reasons
    assert result.metrics.llm_calls == 0


def test_transition_gap_after_anchor_blocks_full_reanchor_readiness() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["source_watermarks"][1].update(
        {
            "scanned_start": "2026-08-10",
            "limitations": [],
            "status": "complete",
            "limitation_kind": None,
        }
    )
    payload = attach_source_observations(
        "Sanitized deterministic readiness fixture.",
        *(SourceObservation(**item) for item in raw["source_observations"]),
    )
    payload = attach_source_watermarks(
        payload,
        *(SourceWatermark(**item) for item in raw["source_watermarks"]),
    )

    result = validate_japanese_anchor_readiness(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-08-10"),
        information_frontier=FRONTIER,
        anchor_frontier=datetime.fromisoformat("2026-08-08T18:00:00+09:00"),
        market_checker=_market_ready,
        news_collector=lambda *_args, **_kwargs: payload,
    )

    assert not result.ready
    assert AnchorReadinessReason.REQUIRED_CAPABILITY_UNAVAILABLE in result.reasons


def test_capability_accepts_an_alternative_and_requires_every_complementary_source(
    monkeypatch,
) -> None:
    profile = anchor_readiness.JAPANESE_ANCHOR_PROFILE
    contracts = []
    for contract in profile.source_contracts:
        if contract.capability is MarketResearchCapability.OFFICIAL_FILING:
            contract = CapabilitySourceContract(
                capability=contract.capability,
                transition_continuity=contract.transition_continuity,
                acceptable_source_sets=(("Unavailable filing source",), ("EDINET",)),
            )
        elif contract.capability is MarketResearchCapability.TIMELY_DISCLOSURE:
            contract = CapabilitySourceContract(
                capability=contract.capability,
                transition_continuity=TransitionContinuityRule.EVENT_STREAM,
                acceptable_source_sets=(("TDnet", "Companion disclosure source"),),
            )
        contracts.append(contract)
    monkeypatch.setattr(
        anchor_readiness,
        "JAPANESE_ANCHOR_PROFILE",
        profile.model_copy(update={"source_contracts": tuple(contracts)}),
    )
    complete_payload = attach_source_watermarks(
        _fixture_payload(),
        SourceWatermark(
            source="Companion disclosure source",
            scanned_start="2026-05-13",
            scanned_end="2026-08-10",
            status="complete",
            returned_records=0,
            reported_records=0,
            information_frontier=FRONTIER.isoformat(),
        ),
    )

    complete = _check(news_payload=complete_payload)
    incomplete = _check()

    assert complete.ready
    assert next(
        item
        for item in complete.capabilities
        if item.capability is MarketResearchCapability.OFFICIAL_FILING
    ).sources == ("EDINET",)
    assert next(
        item
        for item in complete.capabilities
        if item.capability is MarketResearchCapability.TIMELY_DISCLOSURE
    ).sources == ("TDnet", "Companion disclosure source")
    assert not incomplete.ready
    assert AnchorReadinessReason.REQUIRED_CAPABILITY_UNAVAILABLE in incomplete.reasons


@pytest.mark.parametrize(
    "payload",
    [
        ('<!-- tradingagents-source-record:v1 {"source":"EDINET"} -->\n' + _fixture_payload()),
        attach_source_watermarks(
            _fixture_payload(),
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-05-13",
                scanned_end="2026-08-10",
                status="complete",
                returned_records=2,
                reported_records=2,
                information_frontier=FRONTIER.isoformat(),
            ),
        ),
        attach_source_watermarks(
            _fixture_payload(),
            SourceWatermark(
                source="EDINET",
                scanned_start="2026-05-13",
                scanned_end="2026-08-10",
                status="complete",
                returned_records=1,
                reported_records=0,
                information_frontier=FRONTIER.isoformat(),
            ),
        ),
    ],
)
def test_invalid_source_metadata_and_record_counts_fail_closure(payload: str) -> None:
    result = _check(news_payload=payload)

    assert not result.ready
    assert AnchorReadinessReason.INVALID_SOURCE_CLOSURE in result.reasons
