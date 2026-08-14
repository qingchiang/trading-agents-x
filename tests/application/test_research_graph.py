from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

import tradingagents.graph.research_graph as research_graph_module
from tests.factories import analyst_report, research_decision
from tradingagents.application.anchor_readiness import (
    AnchorReadinessResult,
    AnchorReadinessSourceFrontier,
    source_record_versions_digest,
    validate_japanese_anchor_readiness,
)
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalystClaimType,
    ClaimImportance,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    DecisionBrief,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    IssueDisposition,
    MemoryContext,
    MemoryOutcome,
    MemoryRecord,
    ResearchArtifactDraft,
    ResearchRating,
    ResearchWarning,
    RiskReviewAdjustment,
    RiskReviewDisposition,
    RunMetrics,
    RunProfile,
)
from tradingagents.application.market_readiness import MarketDataReadiness
from tradingagents.application.metrics import MetricsCallback
from tradingagents.application.research import (
    CapabilityAttestation,
    CoverageRequirement,
    MarketResearchCapability,
    assemble_full_revision,
    bind_information_frontier,
    derive_forward_research_anchor,
    evaluate_next_update_policy,
)
from tradingagents.application.runtime import RunContext
from tradingagents.application.source_dependencies import is_internal_source_reference
from tradingagents.graph.analyst_synthesis import AnalystAuditDraft, AuditKeyClaimDraft
from tradingagents.graph.deliberation import (
    DecisionNumericDraft,
    JudgeAudit,
    RebuttalAudit,
    ResearchDecisionCoreEnvelope,
)
from tradingagents.graph.research_graph import (
    GraphVisibleRequiredEvidenceError,
    ResearchGraph,
    _evidence_from_record,
    _evidence_warnings,
    _filter_tool_output_at_information_frontier,
)
from tradingagents.provenance import (
    ProvenanceRecord,
    SourceInterval,
    SourceObservation,
    SourceWatermark,
    attach_evidence_span,
    attach_provenance,
    attach_source_observations,
    attach_source_watermarks,
    extract_evidence_spans,
    extract_source_watermarks,
)


def test_tool_output_excludes_post_frontier_content_before_analyst_reasoning() -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    content = attach_source_observations(
        "Post-frontier disclosure must not reach the analyst.",
        SourceObservation(
            source="TDnet",
            record_id="record-1",
            version_id="record-1:v1",
            status="published",
            published_at="2026-07-24 19:00",
            available_at="2026-07-24T19:00:00+09:00",
            title="Late disclosure",
        ),
    )
    message = ToolMessage(
        content=content,
        tool_call_id="call-1",
        name="get_news",
        artifact={"sensitive": "post-frontier"},
    )

    filtered = _filter_tool_output_at_information_frontier(
        {"messages": [message]},
        frontier,
    )

    assert "Post-frontier disclosure" not in filtered["messages"][0].content
    assert filtered["messages"][0].artifact is None


def test_tool_output_fails_closed_for_unattested_current_day_payloads() -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    live_only = ToolMessage(
        content=attach_source_watermarks(
            "Google News result without article availability timestamps.",
            SourceWatermark(
                source="Google News",
                scanned_start="2026-07-24",
                scanned_end="2026-07-24",
                status="complete",
                temporal_scope="live_only",
            ),
        ),
        tool_call_id="call-live",
        name="get_news",
    )
    artifact = ToolMessage(
        content="Current-day analytical overview.",
        tool_call_id="call-artifact",
        name="get_stock_data",
        artifact={
            "schema_version": "1",
            "kind": "source",
            "dataset_id": "ds_fixture",
            "evidence_type": "get_stock_data",
            "source_content": "Late source row",
            "provenance": [
                {
                    "evidence": "get_stock_data",
                    "source": "fixture",
                    "requested": "2026-07-24",
                    "effective": "2026-07-24",
                    "timing": "unknown",
                }
            ],
            "temporal_scope": "point_in_time",
            "analytical_views": {},
        },
    )

    filtered = _filter_tool_output_at_information_frontier(
        {"messages": [live_only, artifact]},
        frontier,
    )

    assert all(
        message.content.startswith("Evidence omitted")
        for message in filtered["messages"]
    )
    assert all(message.artifact is None for message in filtered["messages"])


def test_tool_output_checks_live_only_metadata_even_with_attested_records() -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    content = attach_source_observations(
        "Attested filing mixed with unattested live-only news.",
        SourceObservation(
            source="EDINET",
            record_id="record-1",
            version_id="record-1:v1",
            status="published",
            published_at="2026-07-24 16:00",
            available_at="2026-07-24T16:00:00+09:00",
            title="Timely filing",
        ),
    )
    content = attach_source_watermarks(
        content,
        SourceWatermark(
            source="Google News",
            scanned_start="2026-07-24",
            scanned_end="2026-07-24",
            status="complete",
            temporal_scope="live_only",
        ),
    )
    message = ToolMessage(
        content=content,
        tool_call_id="call-mixed",
        name="get_news",
    )

    filtered = _filter_tool_output_at_information_frontier(
        {"messages": [message]},
        frontier,
    )

    assert filtered["messages"][0].content.startswith("Evidence omitted")


def test_tool_output_admits_safe_pit_and_near_live_spans_independently() -> None:
    frontier = datetime(
        2026,
        8,
        10,
        23,
        59,
        tzinfo=timezone(timedelta(hours=9)),
    )
    pit = attach_evidence_span(
        attach_provenance(
            "PIT DISCLOSURE BODY",
            ProvenanceRecord(
                evidence="filing",
                source="EDINET",
                requested="2026-08-10",
                effective="2026-08-10",
                timing="disclosure-date filtered",
                retrieved_at="2026-08-10T17:00:00+09:00",
            ),
        ),
        temporal_scope="point_in_time",
    )
    near_live = attach_evidence_span(
        attach_provenance(
            "NEAR-LIVE HEADLINE BODY",
            ProvenanceRecord(
                evidence="ticker news",
                source="Google News",
                requested="2026-08-10",
                effective="published through 2026-08-10",
                timing="live non-point-in-time; publication-date filtered",
                retrieved_at="2026-08-14T00:20:00+09:00",
            ),
        ),
        temporal_scope="live_only",
    )

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content=f"{pit}\n{near_live}",
                    tool_call_id="call-source-spans",
                    name="get_news",
                )
            ]
        },
        frontier,
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        sealed_at=datetime(2026, 8, 14, 0, 21, tzinfo=timezone(timedelta(hours=9))),
    )

    content = filtered["messages"][0].content
    assert "PIT DISCLOSURE BODY" in content
    assert "NEAR-LIVE HEADLINE BODY" in content
    assert len(extract_evidence_spans(content)) == 2
    assert filtered["messages"][0].additional_kwargs[
        "evidence_admission_sealed_at"
    ] == "2026-08-14T00:21:00+09:00"


def test_tool_output_withholds_only_ineligible_live_span_body() -> None:
    frontier = datetime(
        2026,
        8,
        10,
        23,
        59,
        tzinfo=timezone(timedelta(hours=9)),
    )
    pit = attach_evidence_span(
        attach_provenance(
            "PIT SIBLING BODY",
            ProvenanceRecord(
                evidence="filing",
                source="EDINET",
                requested="2026-08-10",
                effective="2026-08-09",
                timing="disclosure-date filtered",
            ),
        ),
        temporal_scope="point_in_time",
    )
    stale_live = attach_evidence_span(
        attach_provenance(
            "STALE LIVE BODY",
            ProvenanceRecord(
                evidence="ticker news",
                source="Google News",
                requested="2026-08-10",
                effective="published through 2026-08-10",
                timing="live non-point-in-time; publication-date filtered",
                retrieved_at="2026-08-16T00:20:00+09:00",
            ),
        ),
        temporal_scope="live_only",
    )

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content=f"{pit}\n{stale_live}",
                    tool_call_id="call-stale-span",
                    name="get_news",
                )
            ]
        },
        frontier,
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        sealed_at=datetime(2026, 8, 16, 0, 21, tzinfo=timezone(timedelta(hours=9))),
    )

    content = filtered["messages"][0].content
    assert "PIT SIBLING BODY" in content
    assert "STALE LIVE BODY" not in content
    watermarks = extract_source_watermarks(content)
    assert any(item.source == "Google News" for item in watermarks)
    assert all(item.status == "unavailable" for item in watermarks)
    assert all(item.temporal_scope == "live_only" for item in watermarks)
    assert all(item.limitation_kind == "live_only" for item in watermarks)


def test_near_live_exception_does_not_admit_post_cutoff_pit_span() -> None:
    frontier = datetime(
        2026,
        8,
        14,
        0,
        20,
        tzinfo=timezone(timedelta(hours=9)),
    )
    post_cutoff = attach_evidence_span(
        attach_provenance(
            "POST-CUTOFF PIT BODY",
            ProvenanceRecord(
                evidence="market data",
                source="J-Quants",
                requested="2026-08-10",
                effective="2026-08-12",
                timing="trade-date filtered",
                retrieved_at="2026-08-13T18:00:00+09:00",
            ),
        ),
        temporal_scope="point_in_time",
    )

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content=post_cutoff,
                    tool_call_id="call-post-cutoff-pit",
                    name="get_stock_data",
                )
            ]
        },
        frontier,
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
    )

    assert "POST-CUTOFF PIT BODY" not in filtered["messages"][0].content


def test_tool_output_rejects_live_span_retrieved_after_preseal_bound() -> None:
    frontier = datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc)
    live = attach_evidence_span(
        attach_provenance(
            "FUTURE RETRIEVAL BODY",
            ProvenanceRecord(
                evidence="ticker news",
                source="Google News",
                requested="2026-08-10",
                effective="published through 2026-08-10",
                timing="live non-point-in-time; publication-date filtered",
                retrieved_at="2026-08-14T00:21:00+09:00",
            ),
        ),
        temporal_scope="live_only",
    )

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content=live,
                    tool_call_id="call-future-retrieval",
                    name="get_news",
                )
            ]
        },
        frontier,
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        sealed_at=datetime(2026, 8, 14, 0, 20, tzinfo=timezone(timedelta(hours=9))),
    )

    assert "FUTURE RETRIEVAL BODY" not in filtered["messages"][0].content


def test_tool_output_reuses_checkpointed_preseal_bound_on_replay() -> None:
    frontier = datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc)
    live = attach_evidence_span(
        attach_provenance(
            "REPLAY MUST NOT ADMIT THIS BODY",
            ProvenanceRecord(
                evidence="ticker news",
                source="Google News",
                requested="2026-08-10",
                effective="published through 2026-08-10",
                timing="live non-point-in-time; publication-date filtered",
                retrieved_at="2026-08-14T00:21:00+09:00",
            ),
        ),
        temporal_scope="live_only",
    )
    message = ToolMessage(
        content=live,
        tool_call_id="call-replayed-bound",
        name="get_news",
        additional_kwargs={
            "evidence_admission_sealed_at": "2026-08-14T00:20:00+09:00"
        },
    )

    filtered = _filter_tool_output_at_information_frontier(
        {"messages": [message]},
        frontier,
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
    )

    assert "REPLAY MUST NOT ADMIT THIS BODY" not in filtered["messages"][0].content
    assert filtered["messages"][0].additional_kwargs == message.additional_kwargs


def test_tool_output_fails_closed_for_unsegmented_mixed_temporal_records() -> None:
    frontier = datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc)
    mixed = attach_provenance(
        "UNSEGMENTED MIXED BODY",
        ProvenanceRecord(
            evidence="filing",
            source="EDINET",
            requested="2026-08-10",
            effective="2026-08-10",
            timing="disclosure-date filtered",
            retrieved_at="2026-08-10T12:00:00+09:00",
        ),
        ProvenanceRecord(
            evidence="ticker news",
            source="Google News",
            requested="2026-08-10",
            effective="published through 2026-08-10",
            timing="live non-point-in-time; publication-date filtered",
            retrieved_at="2026-08-14T00:19:00+09:00",
        ),
    )

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content=mixed,
                    tool_call_id="call-mixed-unsegmented",
                    name="get_news",
                )
            ]
        },
        frontier,
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        sealed_at=datetime(2026, 8, 14, 0, 20, tzinfo=timezone(timedelta(hours=9))),
    )

    assert "UNSEGMENTED MIXED BODY" not in filtered["messages"][0].content


def test_tool_output_fails_closed_for_unsegmented_live_and_unknown_records() -> None:
    frontier = datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc)
    mixed = attach_provenance(
        "LIVE PLUS UNKNOWN BODY",
        ProvenanceRecord(
            evidence="ticker news",
            source="Google News",
            requested="2026-08-10",
            effective="published through 2026-08-10",
            timing="live non-point-in-time; publication-date filtered",
            retrieved_at="2026-08-14T00:19:00+09:00",
        ),
        ProvenanceRecord(
            evidence="opaque overlay",
            source="unknown adapter",
            requested="2026-08-10",
            effective="2026-08-10",
            timing="opaque temporal semantics",
            retrieved_at="2026-08-14T00:19:00+09:00",
        ),
    )

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content=mixed,
                    tool_call_id="call-live-unknown",
                    name="get_news",
                )
            ]
        },
        frontier,
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
        sealed_at=datetime(2026, 8, 14, 0, 20, tzinfo=timezone(timedelta(hours=9))),
    )

    assert "LIVE PLUS UNKNOWN BODY" not in filtered["messages"][0].content


def test_tool_output_checks_each_same_day_channel_independently() -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    content = attach_source_observations(
        "Safe observation cannot attest another source or artifact.",
        SourceObservation(
            source="EDINET",
            record_id="record-1",
            version_id="record-1:v1",
            status="published",
            published_at="2026-07-24 16:00",
            available_at="2026-07-24T16:00:00+09:00",
            title="Timely filing",
        ),
    )
    content = attach_source_watermarks(
        content,
        SourceWatermark(
            source="separate-source",
            scanned_start="2026-07-24",
            scanned_end="2026-07-24",
            status="complete",
        ),
    )
    message = ToolMessage(
        content=content,
        tool_call_id="call-independent",
        name="get_news",
        artifact={
            "schema_version": "1",
            "kind": "source",
            "dataset_id": "ds_fixture",
            "evidence_type": "get_news",
            "source_content": "Current-day unattested artifact",
            "provenance": [],
            "temporal_scope": "point_in_time",
            "analytical_views": {},
        },
    )

    filtered = _filter_tool_output_at_information_frontier(
        {"messages": [message]},
        frontier,
    )

    assert filtered["messages"][0].content.startswith("Evidence omitted")
    assert filtered["messages"][0].artifact is None


@pytest.mark.parametrize(
    ("instrument", "source"),
    [
        ("4568.T", "J-Quants adjusted OHLCV"),
        ("NVDA", "yfinance"),
        ("600519.SS", "AKShare"),
    ],
)
def test_cutoff_date_pit_market_artifact_survives_graph_admission(
    instrument: str,
    source: str,
) -> None:
    frontier = datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc)
    artifact = {
        "schema_version": "1",
        "kind": "source",
        "dataset_id": "ds_cutoffmarket",
        "evidence_type": "get_stock_data",
        "source_content": (
            "# Data retrieved on: 2026-08-14 00:20:00\n"
            "Date,Open,High,Low,Close,Volume\n"
            "2026-08-09,99,101,98,100,1000\n"
            "2026-08-10,100,102,99,101,1200"
        ),
        "provenance": [
            {
                "evidence": "get_stock_data",
                "source": source,
                "requested": "2026-08-09 to 2026-08-10",
                "effective": "2026-08-09 to 2026-08-10",
                "timing": "market-date filtered; rows after cutoff excluded",
                "retrieved_at": None,
            }
        ],
        "temporal_scope": "point_in_time",
        "analytical_views": {"row_count": 2, "effective_end": "2026-08-10"},
    }
    message = ToolMessage(
        content="MODEL-SAFE MARKET OVERVIEW",
        artifact=artifact,
        tool_call_id="call-cutoff-market",
        name="get_stock_data",
    )

    filtered = _filter_tool_output_at_information_frontier(
        {"messages": [message]},
        frontier,
        analysis_date=date(2026, 8, 10),
        instrument=instrument,
    )

    assert filtered["messages"][0].content == "MODEL-SAFE MARKET OVERVIEW"
    assert filtered["messages"][0].artifact == artifact


@pytest.mark.parametrize(
    ("temporal_scope", "effective", "timing", "source_content"),
    [
        (
            "point_in_time",
            "2026-08-11",
            "market-date filtered",
            "Date,Close\n2026-08-10,101",
        ),
        ("unknown", "2026-08-10", "unknown", "Date,Close\n2026-08-10,101"),
        (
            "point_in_time",
            "unknown",
            "market-date filtered",
            "Date,Close\n2026-08-10,101",
        ),
        (
            "point_in_time",
            "2026-08-10",
            "market-date filtered",
            "Date,Close\n2026-08-11,102",
        ),
    ],
)
def test_market_artifact_fails_closed_for_unsafe_temporal_metadata(
    temporal_scope: str,
    effective: str,
    timing: str,
    source_content: str,
) -> None:
    artifact = {
        "schema_version": "1",
        "kind": "source",
        "dataset_id": "ds_unsafemarket",
        "evidence_type": "get_stock_data",
        "source_content": source_content,
        "provenance": [
            {
                "evidence": "get_stock_data",
                "source": "fixture",
                "requested": "2026-08-10",
                "effective": effective,
                "timing": timing,
                "retrieved_at": None,
            }
        ],
        "temporal_scope": temporal_scope,
        "analytical_views": {"row_count": 1},
    }

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content="UNSAFE MARKET OVERVIEW",
                    artifact=artifact,
                    tool_call_id="call-unsafe-market",
                    name="get_stock_data",
                )
            ]
        },
        datetime(2026, 8, 10, 23, 59, tzinfo=timezone.utc),
        analysis_date=date(2026, 8, 10),
        instrument="4568.T",
    )

    assert filtered["messages"][0].artifact is None
    assert "UNSAFE MARKET OVERVIEW" not in filtered["messages"][0].content


def test_tool_output_retains_same_source_precisely_attested_before_frontier() -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    content = attach_source_observations(
        "Precisely attested current-day filing.",
        SourceObservation(
            source="EDINET",
            record_id="record-1",
            version_id="record-1:v1",
            status="published",
            published_at="2026-07-24 16:00",
            available_at="2026-07-24T16:00:00+09:00",
            title="Timely filing",
        ),
    )
    content = attach_source_watermarks(
        content,
        SourceWatermark(
            source="EDINET",
            scanned_start="2026-07-24",
            scanned_end="2026-07-24",
            status="complete",
        ),
    )
    message = ToolMessage(
        content=content,
        tool_call_id="call-attested",
        name="get_news",
    )

    filtered = _filter_tool_output_at_information_frontier(
        {"messages": [message]},
        frontier,
    )

    assert filtered["messages"][0].content == content


@pytest.mark.parametrize("as_span", [False, True])
def test_tool_output_omits_unattested_current_day_provenance(
    as_span: bool,
) -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    content = attach_provenance(
        "Current-day provenance without precise availability.",
        ProvenanceRecord(
            evidence="get_news",
            source="provenance-only",
            requested="2026-07-24",
            effective="2026-07-24",
            timing="publication-date filtered",
        ),
    )
    if as_span:
        content = attach_evidence_span(content, temporal_scope="point_in_time")
    message = ToolMessage(
        content=content,
        tool_call_id="call-provenance",
        name="get_news",
    )

    filtered = _filter_tool_output_at_information_frontier(
        {"messages": [message]},
        frontier,
    )

    filtered_content = filtered["messages"][0].content
    assert "Current-day provenance" not in filtered_content
    limitations = extract_source_watermarks(filtered_content)
    assert limitations[0].source == "provenance-only"
    assert limitations[0].status == "unavailable"
    assert limitations[0].limitation_kind == "unknown"


def test_frontier_omission_downgrades_existing_optimistic_watermark() -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    content = attach_source_watermarks(
        "Live source body.",
        SourceWatermark(
            source="Google News",
            scanned_start="2026-07-24",
            scanned_end="2026-07-24",
            status="complete",
            temporal_scope="live_only",
            returned_records=3,
        ),
    )

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id="call-watermark",
                    name="get_news",
                )
            ]
        },
        frontier,
    )

    watermark = extract_source_watermarks(filtered["messages"][0].content)[0]
    assert watermark.status == "unavailable"
    assert watermark.returned_records == 0
    assert watermark.limitation_kind == "unknown"
    assert "could not be attested" in watermark.limitations[-1]


def test_tool_output_omits_metadata_free_content_with_unknown_source_limitation() -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content="Unattested current tool output.",
                    tool_call_id="call-unattested",
                    name="legacy_tool",
                )
            ]
        },
        frontier,
    )

    content = filtered["messages"][0].content
    assert "Unattested current tool output" not in content
    watermark = extract_source_watermarks(content)[0]
    assert watermark.source == "legacy_tool"
    assert watermark.status == "unavailable"
    assert watermark.returned_records == 0
    assert watermark.limitation_kind == "unknown"


def test_tool_output_retains_precisely_retrieved_current_day_provenance() -> None:
    frontier = datetime(
        2026,
        7,
        24,
        18,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    content = attach_provenance(
        "Current-day provenance retrieved before the frontier.",
        ProvenanceRecord(
            evidence="get_news",
            source="provenance-only",
            requested="2026-07-24",
            effective="2026-07-24",
            timing="publication-date filtered",
            retrieved_at="2026-07-24T17:00:00+09:00",
        ),
    )

    filtered = _filter_tool_output_at_information_frontier(
        {
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id="call-provenance-safe",
                    name="get_news",
                )
            ]
        },
        frontier,
    )

    assert filtered["messages"][0].content == content


class _StructuredInvoker:
    def __init__(self, schema, calls):
        self.schema = schema
        self.calls = calls

    def invoke(self, prompt, config=None):
        assert config is None or "metadata" in config
        self.calls.append((self.schema.__name__, prompt))
        refs = tuple(dict.fromkeys(re.findall(r"ev_[a-f0-9]{12}", prompt)))
        memory_refs = tuple(
            dict.fromkeys(
                re.findall(
                    r"memory:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
                    prompt,
                )
            )
        )
        if self.schema is AnalystAuditDraft:
            analyst = re.search(
                r"existing\n(market|social|news|fundamentals) report",
                prompt,
            ).group(1)
            section_id = f"{analyst}.section_1"
            parsed = AnalystAuditDraft(
                confidence=0.6,
                key_claims=(
                    AuditKeyClaimDraft(
                        id=f"{analyst}.claim_1",
                        section_id=section_id,
                        kind=AnalystClaimType.INFERENCE,
                        importance=ClaimImportance.PRIMARY,
                        statement="Fixture evidence is mixed.",
                        implication="The conclusion should preserve uncertainty.",
                        confidence=0.6,
                        evidence_refs=refs[-1:],
                    ),
                ),
                section_source_refs={section_id: refs[-1:]},
            )
        elif self.schema is DebateAgenda:
            parsed = DebateAgenda(
                summary="One material dispute requires resolution.",
                issues=(
                    DebateIssue(
                        id="debate.issue_1",
                        question="Will the fixture mechanism persist?",
                        importance=DebateImportance.MATERIAL,
                    ),
                ),
            )
        elif self.schema is RebuttalAudit:
            parsed = RebuttalAudit(
                addressed_issue_ids=("debate.issue_1",),
                open_issue_ids=("debate.issue_1",),
            )
        elif self.schema is JudgeAudit:
            parsed = JudgeAudit(
                preliminary_rating=ResearchRating.HOLD,
                confidence=0.6,
                issue_dispositions=(
                    IssueDisposition(
                        issue_id="debate.issue_1",
                        status="unresolved",
                    ),
                ),
            )
        elif self.schema is ResearchDecisionCoreEnvelope:
            risk_roles = tuple(
                role
                for role in (
                    "integrated",
                    "aggressive",
                    "neutral",
                    "conservative",
                )
                if f'"{role}"' in prompt
            )
            adjustments = (
                tuple(
                    RiskReviewAdjustment(
                        source_role=role,
                        disposition=RiskReviewDisposition.MODIFIED,
                        subject=f"{role} review",
                        explanation="The committee calibrated the fixture.",
                        evidence_refs=refs[-1:],
                    )
                    for role in risk_roles
                )
                if "REQUIRED RISK REVIEW ROLES:" in prompt
                else ()
            )
            decision = research_decision(
                confidence=0.65,
                thesis="The available evidence supports a balanced conclusion.",
                evidence_refs=refs[-1:],
                memory_refs=memory_refs[:1],
                catalysts=("Evidence improves",),
                risks=("Evidence deteriorates",),
                invalidation_conditions=("The cited evidence is superseded",),
                risk_review_adjustments=adjustments,
            )
            payload = decision.model_dump(mode="json")
            payload.pop("valuation_assessment", None)
            payload.pop("market_reference_levels", None)
            payload.pop("calculation_records", None)
            payload.pop("numeric_audit_status", None)
            for scenario in payload["scenarios"]:
                scenario.pop("reference_ranges", None)
            parsed = ResearchDecisionCoreEnvelope.model_validate(
                {
                    **payload,
                    "numeric_requirements_declared": False,
                    "numeric_requirement_candidates": [],
                }
            )
        elif self.schema is DecisionNumericDraft:
            parsed = DecisionNumericDraft(requested=False)
        else:
            raise AssertionError(self.schema)
        return {"raw": None, "parsed": parsed, "parsing_error": None}


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema, **_kwargs):
        return _StructuredInvoker(schema, self.calls)

    def invoke(self, prompt, config=None):
        assert config is None or "metadata" in config
        assert not isinstance(prompt, list), (
            "research graph must not invoke an LLM evidence-preparation pass"
        )
        call_type = (
            "MarkdownReport"
            if re.search(
                r"You are the (market|social|news|fundamentals) analyst",
                prompt,
            )
            else "ResearchMarkdown"
        )
        self.calls.append((call_type, prompt))
        refs = tuple(dict.fromkeys(re.findall(r"ev_[a-f0-9]{12}", prompt)))
        analyst_match = re.search(
            r"You are the (market|social|news|fundamentals) analyst",
            prompt,
        )
        analyst = analyst_match.group(1) if analyst_match else "market"
        citation = f"[^{refs[-1]}]" if refs else ""
        return AIMessage(
            content=(
                f"# {analyst.title()} analysis\n\n"
                f"Fixture evidence is mixed. {citation}"
            )
        )


class _AnalystSubgraph:
    _REPORT_KEYS = {
        "market": "market_report",
        "social": "sentiment_report",
        "news": "news_report",
        "fundamentals": "fundamentals_report",
    }

    def __init__(self, analyst):
        self.analyst = analyst

    def invoke(self, state, **_kwargs):
        assert state["past_context"] == ""
        assert _kwargs["config"]["metadata"] == {"research_node": f"analyst.{self.analyst}.collect"}
        return {
            **state,
            self._REPORT_KEYS[self.analyst]: (
                f"{self.analyst.title()} evidence is mixed. "
                "The conclusion should preserve uncertainty."
            ),
        }


class _RequiredEvidenceSubgraph(_AnalystSubgraph):
    def invoke(self, state, **kwargs):
        result = super().invoke(state, **kwargs)
        frontier = "2026-08-10T23:59:00+09:00"
        if self.analyst == "news":
            spans = []
            for source in ("EDINET", "TDnet"):
                payload = attach_provenance(
                    "",
                    ProvenanceRecord(
                        evidence="get_news",
                        source=source,
                        requested="2026-07-12 to 2026-08-10",
                        effective="2026-07-12 to 2026-08-10",
                        timing="available; no relevant items in window",
                    ),
                )
                payload = attach_source_watermarks(
                    payload,
                    SourceWatermark(
                        source=source,
                        scanned_start="2026-07-12",
                        scanned_end="2026-08-10",
                        status="complete",
                        returned_records=0,
                        reported_records=0,
                        requested_interval=SourceInterval(
                            start="2026-07-12",
                            end="2026-08-10",
                        ),
                        information_frontier=frontier,
                    ),
                )
                spans.append(
                    attach_evidence_span(payload, temporal_scope="point_in_time")
                )
            message = ToolMessage(
                content="\n".join(spans),
                tool_call_id="required-news",
                name="get_news",
            )
        elif self.analyst == "market":
            message = ToolMessage(
                content="Market artifact is available.",
                tool_call_id="required-market",
                name="get_stock_data",
                artifact={
                    "schema_version": "1",
                    "kind": "source",
                    "dataset_id": "ds_requiredmarket",
                    "evidence_type": "get_stock_data",
                    "source_content": (
                        "Date,Open,High,Low,Close,Volume\n"
                        "2026-08-10,100,102,99,101,1200"
                    ),
                    "provenance": [
                        {
                            "evidence": "get_stock_data",
                            "source": "J-Quants adjusted OHLCV",
                            "requested": "2026-08-10 to 2026-08-10",
                            "effective": "2026-08-10",
                            "timing": "market-date filtered",
                            "retrieved_at": None,
                        }
                    ],
                    "temporal_scope": "point_in_time",
                    "analytical_views": {
                        "row_count": 1,
                        "effective_end": "2026-08-10",
                    },
                },
            )
        elif self.analyst == "fundamentals":
            pit = attach_source_watermarks(
                attach_source_observations(
                    attach_provenance(
                        "J-Quants disclosed fundamentals.",
                        ProvenanceRecord(
                            evidence="get_fundamentals",
                            source="J-Quants official summary",
                            requested="2026-08-10",
                            effective="disclosures <= 2026-08-10",
                            timing="disclosure-date filtered",
                        ),
                    ),
                    SourceObservation(
                        source="J-Quants fundamentals",
                        record_id="4568:2026-08-10",
                        version_id="jquants-fundamentals:4568:2026-08-10",
                        status="published",
                        published_at="2026-08-10 15:00",
                        available_at="2026-08-10T15:00:00+09:00",
                        title="Financial summary",
                        record_kind="fundamental",
                    ),
                ),
                SourceWatermark(
                    source="J-Quants fundamentals",
                    scanned_start="2026-08-10",
                    scanned_end="2026-08-10",
                    status="complete",
                    returned_records=1,
                    reported_records=1,
                    information_frontier=frontier,
                ),
            )
            live = attach_provenance(
                "Forward PE: 20 (analyst consensus, live only).",
                ProvenanceRecord(
                    evidence="get_fundamentals",
                    source="yfinance analyst consensus",
                    requested="2026-08-10",
                    effective="retrieval-time analyst snapshot",
                    timing="live non-point-in-time",
                    retrieved_at="2026-08-12T23:00:00+09:00",
                ),
            )
            message = ToolMessage(
                content=(
                    attach_evidence_span(pit, temporal_scope="point_in_time")
                    + attach_evidence_span(live, temporal_scope="live_only")
                ),
                tool_call_id="required-fundamentals",
                name="get_fundamentals",
            )
        else:
            return result
        return {**result, "messages": [*state["messages"], message]}


_ACCEPTANCE_FRONTIER = "2026-08-10T23:59:00+09:00"
_ACCEPTANCE_RETRIEVED_AT = "2026-08-14T00:15:00+09:00"
_ACCEPTANCE_SEALED_AT = "2026-08-14T00:20:00+09:00"


def _acceptance_news_payload() -> str:
    spans = []
    for source, sentinel in (
        ("EDINET", "EDINET PIT FILING BODY"),
        ("TDnet", "TDNET PIT DISCLOSURE BODY"),
    ):
        version_id = f"{source.casefold()}:4568:2026-08-10:v1"
        payload = attach_source_watermarks(
            attach_source_observations(
                attach_provenance(
                    sentinel,
                    ProvenanceRecord(
                        evidence="get_news",
                        source=source,
                        requested="2026-07-12 to 2026-08-10",
                        effective="2026-08-10",
                        timing="available",
                    ),
                ),
                SourceObservation(
                    source=source,
                    record_id=f"{source.casefold()}:4568:2026-08-10",
                    version_id=version_id,
                    status="published",
                    published_at="2026-08-10 15:00",
                    available_at="2026-08-10T15:00:00+09:00",
                    title=f"{source} acceptance record",
                ),
            ),
            SourceWatermark(
                source=source,
                scanned_start="2026-07-12",
                scanned_end="2026-08-10",
                status="complete",
                returned_records=1,
                reported_records=1,
                requested_interval=SourceInterval(
                    start="2026-07-12",
                    end="2026-08-10",
                ),
                information_frontier=_ACCEPTANCE_FRONTIER,
            ),
        )
        spans.append(attach_evidence_span(payload, temporal_scope="point_in_time"))

    google = attach_source_watermarks(
        attach_source_observations(
            attach_provenance(
                "GOOGLE NEAR LIVE HEADLINE BODY",
                ProvenanceRecord(
                    evidence="get_news",
                    source="Google News",
                    requested="2026-08-10",
                    effective="2026-08-10",
                    timing="live non-point-in-time",
                    retrieved_at=_ACCEPTANCE_RETRIEVED_AT,
                ),
            ),
            SourceObservation(
                source="Google News",
                record_id="google:4568:2026-08-10",
                version_id="google:4568:2026-08-10:v1",
                status="published",
                published_at="2026-08-10 13:00",
                available_at="2026-08-10T13:00:00+09:00",
                title="Near-live acceptance headline",
            ),
        ),
        SourceWatermark(
            source="Google News",
            scanned_start="2026-08-10",
            scanned_end="2026-08-10",
            status="complete",
            temporal_scope="live_only",
            returned_records=1,
            reported_records=1,
        ),
    )
    spans.append(attach_evidence_span(google, temporal_scope="live_only"))
    return "\n".join(spans)


class _OfflineAcceptanceSubgraph(_AnalystSubgraph):
    def invoke(self, state, **kwargs):
        result = super().invoke(state, **kwargs)
        if self.analyst == "news":
            messages = (
                ToolMessage(
                    content=_acceptance_news_payload(),
                    tool_call_id="acceptance-news",
                    name="get_news",
                    additional_kwargs={
                        "evidence_admission_sealed_at": _ACCEPTANCE_SEALED_AT,
                    },
                ),
            )
        elif self.analyst == "market":
            snapshot = attach_source_watermarks(
                attach_source_observations(
                    attach_provenance(
                        "J-QUANTS VERIFIED MARKET SNAPSHOT",
                        ProvenanceRecord(
                            evidence="get_verified_market_snapshot",
                            source="J-Quants adjusted OHLCV",
                            requested="2026-08-10",
                            effective="2026-08-10",
                            timing="market-date filtered",
                        ),
                    ),
                    SourceObservation(
                        source="J-Quants adjusted OHLCV",
                        record_id="jquants-market:4568.T",
                        version_id="jquants-market:4568:2026-08-10:v1",
                        status="published",
                        published_at="2026-08-10",
                        available_at=_ACCEPTANCE_FRONTIER,
                        title="Adjusted market history through 2026-08-10",
                        record_kind="market",
                        adjustment="J-Quants adjusted OHLCV v2",
                        observation_value=101.0,
                        unit="JPY",
                        precision=2,
                    ),
                ),
                SourceWatermark(
                    source="J-Quants adjusted OHLCV",
                    scanned_start="2026-08-10",
                    scanned_end="2026-08-10",
                    status="complete",
                    returned_records=1,
                    reported_records=1,
                    information_frontier=_ACCEPTANCE_FRONTIER,
                ),
            )
            messages = (
                ToolMessage(
                    content="J-Quants adjusted market artifact is available.",
                    tool_call_id="acceptance-market",
                    name="get_stock_data",
                    artifact={
                        "schema_version": "1",
                        "kind": "source",
                        "dataset_id": "ds_acceptancemarket",
                        "evidence_type": "get_stock_data",
                        "source_content": (
                            "# Data retrieved on: 2026-08-14\n"
                            "Date,Open,High,Low,Close,Volume\n"
                            "2026-08-10,100,102,99,101,1200"
                        ),
                        "provenance": [
                            {
                                "evidence": "get_stock_data",
                                "source": "J-Quants adjusted OHLCV",
                                "requested": "2026-08-10 to 2026-08-10",
                                "effective": "2026-08-10",
                                "timing": "market-date filtered",
                            }
                        ],
                        "temporal_scope": "point_in_time",
                        "analytical_views": {
                            "row_count": 1,
                            "effective_end": "2026-08-10",
                        },
                    },
                ),
                ToolMessage(
                    content=snapshot,
                    tool_call_id="acceptance-market-snapshot",
                    name="get_verified_market_snapshot",
                ),
            )
        elif self.analyst == "fundamentals":
            payload = attach_source_watermarks(
                attach_source_observations(
                    attach_provenance(
                        "J-QUANTS PIT FUNDAMENTALS BODY",
                        ProvenanceRecord(
                            evidence="get_fundamentals",
                            source="J-Quants official summary",
                            requested="2026-08-10",
                            effective="disclosures <= 2026-08-10",
                            timing="disclosure-date filtered",
                        ),
                    ),
                    SourceObservation(
                        source="J-Quants fundamentals",
                        record_id="4568:2026-08-10",
                        version_id="jquants-fundamentals:4568:2026-08-10",
                        status="published",
                        published_at="2026-08-10 15:00",
                        available_at="2026-08-10T15:00:00+09:00",
                        title="Financial summary",
                        record_kind="fundamental",
                    ),
                ),
                SourceWatermark(
                    source="J-Quants fundamentals",
                    scanned_start="2026-08-10",
                    scanned_end="2026-08-10",
                    status="complete",
                    returned_records=1,
                    reported_records=1,
                    information_frontier=_ACCEPTANCE_FRONTIER,
                ),
            )
            messages = (
                ToolMessage(
                    content=attach_evidence_span(payload, temporal_scope="point_in_time"),
                    tool_call_id="acceptance-fundamentals",
                    name="get_fundamentals",
                ),
            )
        else:
            return result
        return {**result, "messages": [*state["messages"], *messages]}


def _context(
    app_settings,
    profile: RunProfile,
    analysts=("market", "news"),
    artifact_writer=None,
    memory: MemoryContext | None = None,
) -> RunContext:
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        profile=profile,
        analysts=analysts,
    )
    settings = app_settings.resolve_run(request)
    return RunContext(
        run_id=f"fixture-{profile.value}",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=memory
        or MemoryContext(
            instrument=request.ticker,
            market="America/New_York",
        ),
        instrument_context="The instrument is NVDA.",
        cancel_requested=lambda: False,
        **({"artifact_writer": artifact_writer} if artifact_writer else {}),
    )


def _memory_context() -> MemoryContext:
    run_id = "prior-run"
    return MemoryContext(
        instrument="NVDA",
        market="America/New_York",
        items=(
            MemoryRecord(
                ref=f"memory:{run_id}",
                run_id=run_id,
                scope="same_ticker",
                ticker="NVDA",
                market="America/New_York",
                analysis_date=date(2026, 6, 30),
                decision=research_decision(
                    confidence=0.55,
                    thesis="Prior demand thesis.",
                    evidence_refs=(),
                    catalysts=(),
                    risks=("Demand slowed.",),
                    invalidation_conditions=("Growth missed.",),
                    time_horizon="6-12 months",
                ),
                outcome=MemoryOutcome(
                    benchmark="SPY",
                    observation_start=date(2026, 7, 1),
                    observation_end=date(2026, 7, 8),
                    holding_intervals=5,
                    raw_return=-0.02,
                    alpha_return=-0.01,
                ),
                reflection="Calibration lesson: demand evidence was overweighted.",
            ),
        ),
    )


def _jp_anchor_readiness() -> AnchorReadinessResult:
    frontier = datetime(
        2026,
        8,
        10,
        23,
        59,
        tzinfo=timezone(timedelta(hours=9)),
    )
    sources = (
        ("EDINET", MarketResearchCapability.OFFICIAL_FILING),
        ("TDnet", MarketResearchCapability.TIMELY_DISCLOSURE),
        ("J-Quants adjusted OHLCV", MarketResearchCapability.MARKET_OBSERVATION),
    )
    return AnchorReadinessResult(
        ready=True,
        requested_cutoff=date(2026, 8, 10),
        information_frontier=frontier,
        profile_id="jp-listed-equity-v1",
        capabilities=tuple(
            CapabilityAttestation(
                capability=capability,
                satisfied=True,
                sources=(source,),
            )
            for source, capability in sources
        ),
        source_frontiers=tuple(
            AnchorReadinessSourceFrontier(
                source=source,
                capability=capability,
                status="complete",
                information_frontier=frontier,
                observed_start=(
                    date(2026, 8, 10)
                    if capability is MarketResearchCapability.MARKET_OBSERVATION
                    else date(2026, 7, 12)
                ),
                observed_end=date(2026, 8, 10),
                requested_start=date(2026, 7, 12),
                requested_end=date(2026, 8, 10),
                returned_records=(
                    None
                    if capability is MarketResearchCapability.MARKET_OBSERVATION
                    else 0
                ),
                reported_records=(
                    None
                    if capability is MarketResearchCapability.MARKET_OBSERVATION
                    else 0
                ),
                record_versions_digest=(
                    None
                    if capability is MarketResearchCapability.MARKET_OBSERVATION
                    else source_record_versions_digest(())
                ),
            )
            for source, capability in sources
        ),
        metrics=RunMetrics(),
    )


def test_anchor_readiness_missing_graph_evidence_stops_before_downstream_llms(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    deep = _FakeLLM()
    events: list[dict[str, Any]] = []
    sealed: list[EvidenceBundle] = []
    readiness = _jp_anchor_readiness()
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.STANDARD,
        analysts=("market", "news", "fundamentals"),
    )
    settings = app_settings.resolve_run(request)
    context = RunContext(
        run_id="fixture-required-evidence-gate",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
        instrument_context="The instrument is 4568.T.",
        cancel_requested=lambda: False,
        information_frontier=readiness.information_frontier,
        anchor_readiness=readiness,
        evidence_writer=sealed.append,
    )
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    with pytest.raises(GraphVisibleRequiredEvidenceError) as exc_info:
        graph.execute(
            context,
            checkpoint_thread_id="required-evidence-gate",
            on_event=events.append,
        )

    assert exc_info.value.reason == "graph_visible_required_evidence_missing"
    assert quick.calls == []
    assert deep.calls == []
    assert len(sealed) == 1
    failure = next(
        event
        for event in events
        if event["event_type"] == "research.anchor_evidence_gate_failed"
    )
    assert failure["payload"]["reason"] == "graph_visible_required_evidence_missing"
    assert set(failure["payload"]["missing_sources"]) == {
        "EDINET",
        "TDnet",
        "J-Quants adjusted OHLCV",
        "J-Quants fundamentals",
    }


def test_anchor_readiness_matching_graph_evidence_reaches_synthesis(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _RequiredEvidenceSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    deep = _FakeLLM()
    readiness = _jp_anchor_readiness()
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market", "news", "fundamentals"),
    )
    settings = app_settings.resolve_run(request)
    context = RunContext(
        run_id="fixture-required-evidence-visible",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
        instrument_context="The instrument is 4568.T.",
        cancel_requested=lambda: False,
        information_frontier=readiness.information_frontier,
        anchor_readiness=readiness,
    )
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    execution = graph.execute(
        context,
        checkpoint_thread_id="required-evidence-visible",
    )

    assert execution.evidence.tables
    assert any(
        origin.temporal_scope.value == "live_only"
        for item in execution.evidence.items
        for origin in item.origins
    )
    assert quick.calls


def test_offline_near_live_acceptance_produces_forward_anchor_and_incremental_policy(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _OfflineAcceptanceSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market", "news", "fundamentals"),
    )
    frontier = datetime.fromisoformat(_ACCEPTANCE_FRONTIER)
    readiness = validate_japanese_anchor_readiness(
        request,
        information_frontier=frontier,
        market_checker=lambda _ticker, cutoff: MarketDataReadiness(
            requested_cutoff=cutoff,
            market_effective_date=cutoff,
            observed_bar_date=cutoff,
        ),
        news_collector=lambda *_args, **_kwargs: _acceptance_news_payload(),
    )
    assert readiness.ready is True

    quick = _FakeLLM()
    deep = _FakeLLM()
    settings = app_settings.resolve_run(request)
    context = RunContext(
        run_id="fixture-offline-near-live-acceptance",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
        instrument_context="The instrument is 4568.T.",
        cancel_requested=lambda: False,
        information_frontier=frontier,
        anchor_readiness=readiness,
    )
    execution = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    ).execute(
        context,
        checkpoint_thread_id="offline-near-live-acceptance",
    )

    analyst_prompts = "\n".join(
        prompt for call_type, prompt in quick.calls if call_type == "MarkdownReport"
    )
    for sentinel in (
        "EDINET PIT FILING BODY",
        "TDNET PIT DISCLOSURE BODY",
        "J-QUANTS PIT FUNDAMENTALS BODY",
        "GOOGLE NEAR LIVE HEADLINE BODY",
    ):
        assert sentinel in analyst_prompts
    assert execution.evidence.tables
    market_row = execution.evidence.tables[0].rows[-1]
    assert market_row.cells["date"].raw_value == "2026-08-10"
    assert market_row.cells["close"].raw_value == 101.0

    google_item = next(
        item
        for item in execution.evidence.items
        if any(origin.source == "Google News" for origin in item.origins)
    )
    assert google_item.content == "GOOGLE NEAR LIVE HEADLINE BODY"
    assert google_item.quality is EvidenceQuality.LOW
    assert any(
        origin.temporal_scope.value == "live_only" for origin in google_item.origins
    )
    assert google_item.ref in execution.reports["news"].source_refs

    revision = bind_information_frontier(
        assemble_full_revision(request, execution),
        frontier,
    )
    qualification = derive_forward_research_anchor(revision)
    policy = evaluate_next_update_policy(
        revision,
        instrument=request.ticker,
        mode="experimental",
    )
    google_coverage = next(
        item for item in revision.coverage.domains if item.source == "Google News"
    )

    assert google_coverage.requirement is CoverageRequirement.ADVISORY
    assert qualification.is_forward_research_anchor is True, qualification.model_dump(
        mode="json"
    )
    assert revision.coverage.anchor_qualification == qualification
    assert policy.policy == "incremental_allowed"
    assert policy.reason is None
    assert all(
        not is_internal_source_reference(source)
        for claim in revision.current_state.claims
        for source in claim.required_sources
    )
    assert all(
        not is_internal_source_reference(source)
        for question in revision.current_state.questions
        for source in question.required_sources
    )


def test_empty_ready_manifest_fails_closed_before_synthesis(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    complete = _jp_anchor_readiness()
    readiness = complete.model_copy(
        update={"capabilities": (), "source_frontiers": ()}
    )
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market",),
    )
    settings = app_settings.resolve_run(request)
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=_FakeLLM(),
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    with pytest.raises(GraphVisibleRequiredEvidenceError) as exc_info:
        graph.execute(
            RunContext(
                run_id="fixture-empty-readiness-manifest",
                request=request,
                settings=settings,
                dataflow_config=settings.dataflow_config(app_settings),
                memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
                instrument_context="The instrument is 4568.T.",
                cancel_requested=lambda: False,
                information_frontier=readiness.information_frontier,
                anchor_readiness=readiness,
            ),
            checkpoint_thread_id="empty-readiness-manifest",
        )

    assert set(exc_info.value.missing_capabilities) == {
        "market_observation",
        "official_filing",
        "timely_disclosure",
    }
    assert quick.calls == []


def test_source_frontier_mismatch_fails_before_synthesis(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _RequiredEvidenceSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    complete = _jp_anchor_readiness()
    source_frontiers = tuple(
        item.model_copy(
            update={"observed_start": item.observed_start - timedelta(days=1)}
        )
        if item.source == "EDINET"
        else item
        for item in complete.source_frontiers
    )
    readiness = complete.model_copy(update={"source_frontiers": source_frontiers})
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market", "news"),
    )
    settings = app_settings.resolve_run(request)
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=_FakeLLM(),
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    with pytest.raises(GraphVisibleRequiredEvidenceError) as exc_info:
        graph.execute(
            RunContext(
                run_id="fixture-readiness-frontier-mismatch",
                request=request,
                settings=settings,
                dataflow_config=settings.dataflow_config(app_settings),
                memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
                instrument_context="The instrument is 4568.T.",
                cancel_requested=lambda: False,
                information_frontier=readiness.information_frontier,
                anchor_readiness=readiness,
            ),
            checkpoint_thread_id="readiness-frontier-mismatch",
        )

    assert exc_info.value.missing_sources == ("EDINET",)
    assert exc_info.value.missing_capabilities == ("official_filing",)
    assert quick.calls == []


def test_source_frontier_limitation_mismatch_fails_before_synthesis(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _RequiredEvidenceSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    complete = _jp_anchor_readiness()
    source_frontiers = tuple(
        item.model_copy(update={"limitations": ("Expected archive limitation.",)})
        if item.source == "TDnet"
        else item
        for item in complete.source_frontiers
    )
    readiness = complete.model_copy(update={"source_frontiers": source_frontiers})
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market", "news"),
    )
    settings = app_settings.resolve_run(request)
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=_FakeLLM(),
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    with pytest.raises(GraphVisibleRequiredEvidenceError) as exc_info:
        graph.execute(
            RunContext(
                run_id="fixture-readiness-limitation-mismatch",
                request=request,
                settings=settings,
                dataflow_config=settings.dataflow_config(app_settings),
                memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
                instrument_context="The instrument is 4568.T.",
                cancel_requested=lambda: False,
                information_frontier=readiness.information_frontier,
                anchor_readiness=readiness,
            ),
            checkpoint_thread_id="readiness-limitation-mismatch",
        )

    assert exc_info.value.missing_sources == ("TDnet",)
    assert exc_info.value.missing_capabilities == ("timely_disclosure",)
    assert quick.calls == []


def test_source_frontier_limitation_kind_mismatch_fails_before_synthesis(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _RequiredEvidenceSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    complete = _jp_anchor_readiness()
    source_frontiers = tuple(
        item.model_copy(update={"limitation_kind": "archive_truncation"})
        if item.source == "TDnet"
        else item
        for item in complete.source_frontiers
    )
    readiness = complete.model_copy(update={"source_frontiers": source_frontiers})
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market", "news"),
    )
    settings = app_settings.resolve_run(request)
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=_FakeLLM(),
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    with pytest.raises(GraphVisibleRequiredEvidenceError) as exc_info:
        graph.execute(
            RunContext(
                run_id="fixture-readiness-limitation-kind-mismatch",
                request=request,
                settings=settings,
                dataflow_config=settings.dataflow_config(app_settings),
                memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
                instrument_context="The instrument is 4568.T.",
                cancel_requested=lambda: False,
                information_frontier=readiness.information_frontier,
                anchor_readiness=readiness,
            ),
            checkpoint_thread_id="readiness-limitation-kind-mismatch",
        )

    assert exc_info.value.missing_sources == ("TDnet",)
    assert exc_info.value.missing_capabilities == ("timely_disclosure",)
    assert quick.calls == []


def test_source_record_closure_mismatch_fails_before_synthesis(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _RequiredEvidenceSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    complete = _jp_anchor_readiness()
    source_frontiers = tuple(
        item.model_copy(
            update={
                "returned_records": 1,
                "reported_records": 1,
                "record_versions_digest": source_record_versions_digest(
                    ("edinet-record:v1",)
                ),
            }
        )
        if item.source == "EDINET"
        else item
        for item in complete.source_frontiers
    )
    readiness = complete.model_copy(update={"source_frontiers": source_frontiers})
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market", "news"),
    )
    settings = app_settings.resolve_run(request)
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=_FakeLLM(),
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    with pytest.raises(GraphVisibleRequiredEvidenceError) as exc_info:
        graph.execute(
            RunContext(
                run_id="fixture-readiness-record-closure-mismatch",
                request=request,
                settings=settings,
                dataflow_config=settings.dataflow_config(app_settings),
                memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
                instrument_context="The instrument is 4568.T.",
                cancel_requested=lambda: False,
                information_frontier=readiness.information_frontier,
                anchor_readiness=readiness,
            ),
            checkpoint_thread_id="readiness-record-closure-mismatch",
        )

    assert exc_info.value.missing_sources == ("EDINET",)
    assert exc_info.value.missing_capabilities == ("official_filing",)
    assert quick.calls == []


def test_unknown_readiness_profile_fails_closed_before_synthesis(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    complete = _jp_anchor_readiness()
    readiness = complete.model_copy(
        update={
            "profile_id": "unknown-profile",
            "capabilities": (),
            "source_frontiers": (),
        }
    )
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market",),
    )
    settings = app_settings.resolve_run(request)
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=_FakeLLM(),
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    with pytest.raises(GraphVisibleRequiredEvidenceError) as exc_info:
        graph.execute(
            RunContext(
                run_id="fixture-unknown-readiness-profile",
                request=request,
                settings=settings,
                dataflow_config=settings.dataflow_config(app_settings),
                memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
                instrument_context="The instrument is 4568.T.",
                cancel_requested=lambda: False,
                information_frontier=readiness.information_frontier,
                anchor_readiness=readiness,
            ),
            checkpoint_thread_id="unknown-readiness-profile",
        )

    assert set(exc_info.value.missing_capabilities) == {
        "market_observation",
        "official_filing",
        "timely_disclosure",
    }
    assert quick.calls == []


def test_allow_non_anchor_context_does_not_apply_required_evidence_gate(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts
        },
    )
    request = AnalysisRequest(
        ticker="4568.T",
        analysis_date="2026-08-10",
        profile=RunProfile.FAST,
        analysts=("market",),
        anchor_readiness="allow_non_anchor",
    )
    settings = app_settings.resolve_run(request)
    quick = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=_FakeLLM(),
        profile=request.profile,
        selected_analysts=request.analysts,
        metrics=MetricsCallback(),
    )

    execution = graph.execute(
        RunContext(
            run_id="fixture-allow-non-anchor",
            request=request,
            settings=settings,
            dataflow_config=settings.dataflow_config(app_settings),
            memory=MemoryContext(instrument=request.ticker, market="Asia/Tokyo"),
            instrument_context="The instrument is 4568.T.",
            cancel_requested=lambda: False,
            information_frontier=_jp_anchor_readiness().information_frontier,
        ),
        checkpoint_thread_id="allow-non-anchor",
    )

    assert execution.evidence.items
    assert quick.calls


@pytest.mark.parametrize(
    ("profile", "required_nodes", "forbidden_nodes"),
    [
        (
            RunProfile.FAST,
            {
                "analyst.market",
                "analyst.news",
                "committee.final",
                "committee.final.serialize",
            },
            {"case.bull", "judge.research", "risk.review"},
        ),
        (
            RunProfile.STANDARD,
            {
                "case.bull",
                "case.bear",
                "debate.agenda",
                "rebuttal.bull",
                "rebuttal.bear",
                "judge.research",
                "risk.review",
                "committee.final",
                "committee.final.serialize",
            },
            {"risk.aggressive", "risk.conservative"},
        ),
        (
            RunProfile.DEEP,
            {
                "rebuttal.bull",
                "rebuttal.bear",
                "risk.aggressive",
                "risk.neutral",
                "risk.conservative",
                "committee.final",
                "committee.final.serialize",
            },
            {"risk.review"},
        ),
    ],
)
def test_profiles_share_contract_but_use_distinct_topologies(
    app_settings,
    monkeypatch,
    profile,
    required_nodes,
    forbidden_nodes,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
    )
    quick = _FakeLLM()
    deep = _FakeLLM()
    events: list[dict[str, Any]] = []
    artifacts: list[ResearchArtifactDraft] = []
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=profile,
        selected_analysts=("market", "news"),
        metrics=MetricsCallback(),
    )

    execution = graph.execute(
        _context(app_settings, profile, artifact_writer=artifacts.append),
        checkpoint_thread_id=f"profile:{profile.value}",
        on_event=events.append,
    )

    completed = {event["node"] for event in events if event["event_type"] == "node.completed"}
    assert required_nodes <= completed
    assert not forbidden_nodes & completed
    assert set(execution.reports) == {"market", "news"}
    assert execution.evidence.version == "8"
    assert execution.evidence.digest
    assert execution.decision.rating is ResearchRating.HOLD
    valid_refs = {item.ref for item in execution.evidence.items}
    assert set(execution.decision.evidence_refs) <= valid_refs
    event_positions = {
        (event["event_type"], event["node"]): index
        for index, event in enumerate(events)
        if event["event_type"] in {"node.started", "node.completed"}
    }
    assert event_positions[("node.completed", "evidence.seal")] < min(
        event_positions[("node.started", f"analyst.{analyst}")]
        for analyst in ("market", "news")
    )
    assert max(
        event_positions[("node.completed", f"analyst.{analyst}")]
        for analyst in ("market", "news")
    ) < event_positions[("node.completed", "reports.ready")]
    assert not any(
        node.endswith(".prepare")
        for node in graph.metrics.snapshot().node_metrics
    )
    if profile is not RunProfile.FAST:
        agenda = next(
            artifact for artifact in artifacts if artifact.stage == "agenda"
        )
        assert agenda.generation_observations[0].client_role == (
            "deep_reasoning" if profile is RunProfile.DEEP else "quick_reasoning"
        )
    if profile is RunProfile.STANDARD:
        node_metrics = graph.metrics.snapshot().node_metrics
        assert {
                "case.bull.write",
                "case.bull.audit",
                "committee.final.reason",
                "committee.final.serialize.core",
                "committee.final.serialize.numeric",
            } <= set(node_metrics)
        assert not any(node.endswith(".prepare") for node in node_metrics)
        assert "case.bull" not in node_metrics
        decision_artifact = next(
            artifact for artifact in artifacts if artifact.stage == "decision"
        )
        assert (
            decision_artifact.prompt_version
            == "final-committee-v14-dimensionless-display-scale"
        )
        final_prompt = next(
            prompt
            for schema, prompt in deep.calls
            if schema == "ResearchDecisionCoreEnvelope"
        )
        assert "DECISION SYNTHESIS BRIEF:" in final_prompt
        assert "RESEARCH CONTEXT:" not in final_prompt
        assert "REQUIRED RISK REVIEW ROLES:" in final_prompt
        final_reasoning_prompt = next(
            prompt
            for schema, prompt in deep.calls
            if schema == "ResearchMarkdown" and "SCENARIO ASSUMPTION READABILITY:" in prompt
        )
        assert "Analyst EPS consensus rises to JPY 185-195 per share" in final_reasoning_prompt
        assert "PERCENTAGE CALCULATION CONTRACT:" in final_reasoning_prompt
        assert "formulas must return a fractional ratio" in final_reasoning_prompt
        assert "For every named input, state its exact value" in final_reasoning_prompt
        assert "Evidence refs that establish its date" in final_reasoning_prompt
        assert "Mark pure constants as constants without date Evidence" in (
            final_reasoning_prompt
        )
        assert "every numeric value used by the formula" in final_reasoning_prompt
        agenda_prompt = next(
            prompt for schema, prompt in quick.calls if schema == "DebateAgenda"
        )
        assert '"evidence_catalog"' not in agenda_prompt
        assert '"analyst_reports"' not in agenda_prompt
        agenda_context_event = next(
            event
            for event in events
            if event["event_type"] == "node.context_prepared"
            and event["node"] == "debate.agenda.context"
        )
        assert agenda_context_event["payload"]["catalog_items"] == 0
        assert agenda_context_event["payload"]["catalog_tables"] == 0


@pytest.mark.parametrize(
    ("profile", "quick_schemas", "deep_schemas"),
    (
        (
            RunProfile.FAST,
            {
                "MarkdownReport",
                "AnalystAuditDraft",
            },
            {
                "ResearchDecisionCoreEnvelope",
                "DecisionNumericDraft",
                "ResearchMarkdown",
            },
        ),
        (
            RunProfile.STANDARD,
            {
                "MarkdownReport",
                "AnalystAuditDraft",
                "DebateAgenda",
                "RebuttalAudit",
                "ResearchMarkdown",
            },
            {
                "JudgeAudit",
                "ResearchDecisionCoreEnvelope",
                "DecisionNumericDraft",
                "ResearchMarkdown",
            },
        ),
        (
            RunProfile.DEEP,
            {
                "MarkdownReport",
                "AnalystAuditDraft",
            },
            {
                "DebateAgenda",
                "RebuttalAudit",
                "JudgeAudit",
                "ResearchDecisionCoreEnvelope",
                "DecisionNumericDraft",
                "ResearchMarkdown",
            },
        ),
    ),
)
def test_profiles_route_quality_roles_to_the_configured_model_tier(
    app_settings,
    monkeypatch,
    profile: RunProfile,
    quick_schemas: set[str],
    deep_schemas: set[str],
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
    )
    quick = _FakeLLM()
    deep = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=profile,
        selected_analysts=("market",),
    )

    graph.execute(
        _context(app_settings, profile, analysts=("market",)),
        checkpoint_thread_id=f"model-routing:{profile.value}",
    )

    assert {schema for schema, _prompt in quick.calls} == quick_schemas
    assert {schema for schema, _prompt in deep.calls} == deep_schemas


@pytest.mark.parametrize(
    ("profile", "role", "expected_tier"),
    (
        (RunProfile.FAST, "bull", "quick"),
        (RunProfile.STANDARD, "bull", "quick"),
        (RunProfile.STANDARD, "risk", "quick"),
        (RunProfile.DEEP, "bull", "deep"),
        (RunProfile.DEEP, "risk", "deep"),
    ),
)
def test_profile_keeps_reasoning_and_serializer_on_the_same_model_tier(
    profile: RunProfile,
    role: str,
    expected_tier: str,
) -> None:
    quick = _FakeLLM()
    deep = _FakeLLM()
    quick_serializer = _FakeLLM()
    deep_serializer = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        quick_serializer_llm=quick_serializer,
        deep_serializer_llm=deep_serializer,
        profile=profile,
        selected_analysts=("market",),
    )
    spec = research_graph_module._PERSPECTIVE_SPECS[role]

    if expected_tier == "deep":
        assert graph._deliberation_llm(spec) is deep
        assert graph._deliberation_serializer_llm(spec) is deep_serializer
    else:
        assert graph._deliberation_llm(spec) is quick
        assert graph._deliberation_serializer_llm(spec) is quick_serializer


def test_analyst_core_uses_the_dedicated_serializer_client(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    reasoning = _FakeLLM()
    serializer = _FakeLLM()
    deep = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=reasoning,
        deep_llm=deep,
        quick_serializer_llm=serializer,
        profile=RunProfile.FAST,
        selected_analysts=("market",),
    )

    graph.execute(
        _context(
            app_settings,
            RunProfile.FAST,
            analysts=("market",),
        ),
        checkpoint_thread_id="dedicated-serializer",
    )

    assert all(
        schema != "AnalystAuditDraft"
        for schema, _prompt in reasoning.calls
    )
    assert {
        schema for schema, _prompt in serializer.calls
    } == {"AnalystAuditDraft"}
    assert {schema for schema, _prompt in reasoning.calls} == {
        "MarkdownReport",
    }


def test_final_numeric_uses_reasoning_client_while_core_uses_serializer(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    reasoning = _FakeLLM()
    serializer = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=reasoning,
        deep_serializer_llm=serializer,
        profile=RunProfile.FAST,
        selected_analysts=("market",),
    )

    graph.execute(
        _context(
            app_settings,
            RunProfile.FAST,
            analysts=("market",),
        ),
        checkpoint_thread_id="final-numeric-reasoning-client",
    )

    assert "ResearchDecisionCoreEnvelope" in {
        schema for schema, _prompt in serializer.calls
    }
    assert "DecisionNumericDraft" not in {
        schema for schema, _prompt in serializer.calls
    }
    assert "DecisionNumericDraft" in {
        schema for schema, _prompt in reasoning.calls
    }
    assert "ResearchDecisionCoreEnvelope" not in {
        schema for schema, _prompt in reasoning.calls
    }


def test_debate_agenda_uses_reasoning_client_not_serializer(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    reasoning = _FakeLLM()
    serializer = _FakeLLM()
    deep = _FakeLLM()
    deep_serializer = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=reasoning,
        deep_llm=deep,
        quick_serializer_llm=serializer,
        deep_serializer_llm=deep_serializer,
        profile=RunProfile.STANDARD,
        selected_analysts=("market",),
    )

    graph.execute(
        _context(
            app_settings,
            RunProfile.STANDARD,
            analysts=("market",),
        ),
        checkpoint_thread_id="agenda-reasoning-client",
    )

    assert "DebateAgenda" in {
        schema for schema, _prompt in reasoning.calls
    }
    assert "DebateAgenda" not in {
        schema for schema, _prompt in serializer.calls
    }


@pytest.mark.parametrize(
    ("profile", "decision_prompt_count"),
    (
        (RunProfile.FAST, 1),
        (RunProfile.STANDARD, 2),
        (RunProfile.DEEP, 2),
    ),
)
def test_memory_only_enters_profile_decision_nodes_and_refs_are_whitelisted(
    app_settings,
    monkeypatch,
    profile,
    decision_prompt_count,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
    )
    quick = _FakeLLM()
    deep = _FakeLLM()
    memory = _memory_context()
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=profile,
        selected_analysts=("market",),
    )

    execution = graph.execute(
        _context(
            app_settings,
            profile,
            analysts=("market",),
            memory=memory,
        ),
        checkpoint_thread_id=f"memory:{profile.value}",
    )

    calls = [*quick.calls, *deep.calls]
    decision_prompts = [
        prompt
        for schema, prompt in calls
        if schema == "ResearchMarkdown"
        and (
            "Research Judge" in str(prompt)
            or "Final Research Committee" in str(prompt)
        )
    ]
    memory_prompts = [
        str(prompt)
        for _schema, prompt in calls
        if "Calibration lesson: demand evidence was overweighted."
        in str(prompt)
    ]
    assert len(decision_prompts) == decision_prompt_count
    assert all(
        "Calibration lesson: demand evidence was overweighted." in prompt
        for prompt in decision_prompts
    )
    assert all(
        (
            "Research Judge" in prompt
            or "Final Research Committee" in prompt
            or "DECISION SYNTHESIS BRIEF" in prompt
        )
        for prompt in memory_prompts
    )
    assert execution.decision.memory_refs == memory.refs
    assert "memory:invented" not in execution.decision.memory_refs
    assert not any(ref.startswith("memory:") for ref in execution.decision.evidence_refs)


def test_graph_emits_only_typed_visible_research_artifacts(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
    )
    llm = _FakeLLM()
    artifacts: list[ResearchArtifactDraft] = []
    graph = ResearchGraph(
        quick_llm=llm,
        deep_llm=llm,
        profile=RunProfile.STANDARD,
        selected_analysts=("market", "news"),
    )

    graph.execute(
        _context(
            app_settings,
            RunProfile.STANDARD,
            artifact_writer=artifacts.append,
        ),
        checkpoint_thread_id="typed-artifacts",
    )

    assert {(artifact.stage, artifact.role) for artifact in artifacts} == {
        ("analyst", "market"),
        ("analyst", "news"),
        ("case", "bull"),
        ("case", "bear"),
        ("agenda", "moderator"),
        ("rebuttal", "bull"),
        ("rebuttal", "bear"),
        ("judge", "research_judge"),
        ("risk", "integrated"),
        ("decision_brief", "final_committee"),
        ("decision", "final_committee"),
    }
    assert all(
        artifact.content_type
        in {
            "analyst_report",
            "research_case",
            "debate_agenda",
            "rebuttal_review",
            "judge_draft",
            "risk_review",
            "decision_brief",
            "research_decision",
        }
        for artifact in artifacts
    )
    assert all("messages" not in artifact.content.model_fields for artifact in artifacts)
    assert {
        artifact.generation_method.value for artifact in artifacts if artifact.stage != "analyst"
    } == {"tool_call", "markdown_audited"}
    assert {
        (artifact.role, artifact.prompt_version)
        for artifact in artifacts
        if artifact.stage == "analyst"
    } == {
        ("market", "analyst-market-v6-sealed-context"),
        ("news", "analyst-news-v6-sealed-context"),
    }
    assert all(artifact.prompt_version != "research-v1" for artifact in artifacts)
    agenda = next(artifact for artifact in artifacts if artifact.stage == "agenda")
    assert [
        observation.model_dump(mode="json")
        for observation in agenda.generation_observations
    ] == [
        {
            "node": "debate.agenda.serialize",
            "task_kind": "semantic_structured",
            "client_role": "quick_reasoning",
            "generation_method": "tool_call",
        }
    ]
    final = next(artifact for artifact in artifacts if artifact.stage == "decision")
    assert [
        observation.model_dump(mode="json")
        for observation in final.generation_observations
    ] == [
        {
            "node": "committee.final.serialize.core",
            "task_kind": "schema_serialization",
            "client_role": "deep_serializer",
            "generation_method": "tool_call",
        },
        {
            "node": "committee.final.serialize.numeric",
            "task_kind": "semantic_structured",
            "client_role": "deep_reasoning",
            "generation_method": "tool_call",
        },
    ]
    brief = next(
        artifact.content
        for artifact in artifacts
        if isinstance(artifact.content, DecisionBrief)
    )
    assert brief.markdown


def test_retry_after_final_serialization_reuses_checkpointed_brief(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    llm = _FakeLLM()
    artifacts: list[ResearchArtifactDraft] = []
    graph = ResearchGraph(
        quick_llm=llm,
        deep_llm=llm,
        profile=RunProfile.FAST,
        selected_analysts=("market",),
    )
    context = _context(
        app_settings,
        RunProfile.FAST,
        analysts=("market",),
        artifact_writer=artifacts.append,
    )
    checkpointer = MemorySaver()
    original = research_graph_module.invoke_research_decision
    serializer_calls = 0

    def fail_once(*args, **kwargs):
        nonlocal serializer_calls
        serializer_calls += 1
        if serializer_calls == 1:
            raise RuntimeError("fixture final serialization failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        research_graph_module,
        "invoke_research_decision",
        fail_once,
    )

    with pytest.raises(RuntimeError, match="fixture final serialization failure"):
        graph.execute(
            context,
            checkpointer=checkpointer,
            checkpoint_thread_id="final-brief-retry",
        )

    assert sum(
        isinstance(artifact.content, DecisionBrief) for artifact in artifacts
    ) == 1
    final_reasoning_calls = sum(
        schema == "ResearchMarkdown"
        and "SCENARIO ASSUMPTION READABILITY:" in prompt
        for schema, prompt in llm.calls
    )

    execution = graph.execute(
        context,
        checkpointer=checkpointer,
        checkpoint_thread_id="final-brief-retry",
        resume=True,
    )

    assert execution.decision.rating is ResearchRating.HOLD
    assert serializer_calls == 2
    assert sum(
        isinstance(artifact.content, DecisionBrief) for artifact in artifacts
    ) == 1
    assert (
        sum(
            schema == "ResearchMarkdown"
            and "SCENARIO ASSUMPTION READABILITY:" in prompt
            for schema, prompt in llm.calls
        )
        == final_reasoning_calls
    )


def test_deep_debate_stops_when_rebuttals_add_no_new_information(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
    )
    llm = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=llm,
        deep_llm=llm,
        profile=RunProfile.DEEP,
        selected_analysts=("market",),
    )

    execution = graph.execute(
        _context(app_settings, RunProfile.DEEP, analysts=("market",)),
        checkpoint_thread_id="deep-stop",
    )

    assert execution.state["rebuttal_round"] <= 3


def test_retry_reuses_checkpointed_analyst_tool_artifacts(
    app_settings,
    monkeypatch,
) -> None:
    subgraph = _AnalystSubgraph("market")
    collection_calls = 0
    original_invoke = subgraph.invoke

    def counted_invoke(state, **kwargs):
        nonlocal collection_calls
        collection_calls += 1
        return original_invoke(state, **kwargs)

    subgraph.invoke = counted_invoke
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda _self: {"market": subgraph},
    )
    original_synthesis = research_graph_module._invoke_analyst_report
    synthesis_calls = 0

    def fail_once(*args, **kwargs):
        nonlocal synthesis_calls
        synthesis_calls += 1
        if synthesis_calls == 1:
            raise RuntimeError("fixture synthesis failure")
        return original_synthesis(*args, **kwargs)

    monkeypatch.setattr(
        research_graph_module,
        "_invoke_analyst_report",
        fail_once,
    )
    graph = ResearchGraph(
        quick_llm=_FakeLLM(),
        deep_llm=_FakeLLM(),
        profile=RunProfile.FAST,
        selected_analysts=("market",),
    )
    context = _context(
        app_settings,
        RunProfile.FAST,
        analysts=("market",),
    )
    checkpointer = MemorySaver()

    with pytest.raises(RuntimeError, match="fixture synthesis failure"):
        graph.execute(
            context,
            checkpointer=checkpointer,
            checkpoint_thread_id="analyst-artifact-retry",
        )

    execution = graph.execute(
        context,
        checkpointer=checkpointer,
        checkpoint_thread_id="analyst-artifact-retry",
        resume=True,
    )

    assert execution.decision.rating is ResearchRating.HOLD
    assert collection_calls == 1
    assert synthesis_calls == 2


def test_future_dated_provenance_is_withheld_before_bundle_sealing() -> None:
    item = _evidence_from_record(
        ProvenanceRecord(
            evidence="Future filing",
            source="fixture",
            requested="2026-07-24",
            effective="2026-07-25",
            timing="vendor returned a future record",
        ),
        requested_date=date(2026, 7, 24),
        content="This future payload must not reach any agent.",
    )

    assert item.effective_date is None
    assert item.content is None
    assert item.quality is EvidenceQuality.UNAVAILABLE
    assert "future-dated evidence withheld" in item.provenance["timing"]


def test_analyst_warning_is_derived_from_evidence_quality() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="historical price",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Fixture data.",
        quality=EvidenceQuality.LOW,
    )
    warnings = _evidence_warnings([item])

    assert len(warnings) == 1
    assert warnings[0].message == ("historical price from fixture has low evidence quality.")
    assert warnings[0].evidence_ref == item.ref


def test_sentiment_confidence_and_fallback_warning_reach_typed_handoff() -> None:
    warning = ResearchWarning(
        code="agent.structured_output_fallback",
        message="Sentiment output used the free-text fallback.",
        source="Sentiment Analyst",
    )

    report = analyst_report(
        analyst="social",
        confidence=0.55,
        warnings=(warning,),
        narrative="Preserved structured sentiment report.",
    )

    assert report.confidence == 0.55
    assert report.warnings == (warning,)
