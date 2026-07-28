from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from langchain_core.messages import ToolMessage

from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    RunExport,
    RunStatus,
    RunView,
)
from tradingagents.application.exporting import render_run_export_markdown
from tradingagents.graph.research_graph import (
    _collect_evidence,
    _evidence_from_records,
    _evidence_prompt_index,
)
from tradingagents.provenance import (
    ProvenanceRecord,
    attach_provenance,
)


def _record(
    evidence: str,
    source: str,
    *,
    effective: str = "2026-07-24",
    timing: str = "point-in-time available",
) -> ProvenanceRecord:
    return ProvenanceRecord(
        evidence=evidence,
        source=source,
        requested="2026-07-24",
        effective=effective,
        timing=timing,
        retrieved_at="2026-07-24T12:00:00Z",
    )


def test_composite_tool_payload_creates_one_item_with_all_origins() -> None:
    records = (
        _record("filing", "EDINET", effective="2026-07-23"),
        _record("market data", "JPX", effective="2026-07-24"),
    )
    content = attach_provenance("ONE SHARED BODY", *records)
    narrative = """Report.

## Data Provenance

| Evidence | Source | Requested / cutoff | Effective date / window | Timing status |
|---|---|---|---|---|
| filing | EDINET | 2026-07-24 | 2026-07-23 | point-in-time available |
| market data | JPX | 2026-07-24 | 2026-07-24 | point-in-time available |
"""

    items = _collect_evidence(
        [ToolMessage(content=content, tool_call_id="fixture")],
        narrative,
        requested_date=date(2026, 7, 24),
        analyst="fundamentals",
    )

    assert len(items) == 1
    item = items[0]
    assert item.source == "composite"
    assert item.evidence_type == "composite tool response"
    assert item.content == "ONE SHARED BODY"
    assert item.effective_date == date(2026, 7, 24)
    assert item.quality is EvidenceQuality.HIGH
    assert [(origin.source, origin.evidence_type) for origin in item.origins] == [
        ("EDINET", "filing"),
        ("JPX", "market data"),
    ]


def test_exact_prefetched_bodies_are_aggregated_with_all_origins() -> None:
    blocks = [
        {
            "content": "ONE SHARED PREFETCH BODY",
            "records": [
                {
                    "evidence": "filing",
                    "source": "EDINET",
                    "requested": "2026-07-24",
                    "effective": "2026-07-23",
                    "timing": "publication-date filtered",
                    "retrieved_at": None,
                }
            ],
        },
        {
            "content": "ONE SHARED PREFETCH BODY",
            "records": [
                {
                    "evidence": "market data",
                    "source": "JPX",
                    "requested": "2026-07-24",
                    "effective": "2026-07-24",
                    "timing": "point-in-time available",
                    "retrieved_at": None,
                }
            ],
        },
    ]

    items = _collect_evidence(
        [],
        "Report.",
        requested_date=date(2026, 7, 24),
        analyst="social",
        prefetched_blocks=blocks,
    )

    assert len(items) == 1
    assert items[0].content == "ONE SHARED PREFETCH BODY"
    assert [origin.source for origin in items[0].origins] == [
        "EDINET",
        "JPX",
    ]


def test_composite_quality_is_low_for_mixed_or_fallback_origins() -> None:
    item = _evidence_from_records(
        (
            _record("filing", "EDINET"),
            _record(
                "market data",
                "fallback vendor",
                timing="fallback source used",
            ),
        ),
        requested_date=date(2026, 7, 24),
        content="Shared body.",
    )

    assert item.quality is EvidenceQuality.LOW
    assert item.fallback is True
    assert [origin.quality for origin in item.origins] == [
        EvidenceQuality.HIGH,
        EvidenceQuality.LOW,
    ]


def test_composite_quality_is_unavailable_when_every_origin_is_unavailable() -> None:
    item = _evidence_from_records(
        (
            _record("filing", "EDINET", timing="source unavailable"),
            _record("market data", "JPX", timing="retrieval failed"),
        ),
        requested_date=date(2026, 7, 24),
        content="No usable payload.",
    )

    assert item.quality is EvidenceQuality.UNAVAILABLE


def test_any_future_origin_withholds_the_entire_composite_body() -> None:
    item = _evidence_from_records(
        (
            _record("filing", "EDINET", effective="2026-07-23"),
            _record("market data", "JPX", effective="2026-07-25"),
        ),
        requested_date=date(2026, 7, 24),
        content="The future value must not leak through the other origin.",
    )

    assert item.content is None
    assert item.effective_date == date(2026, 7, 23)
    assert item.quality is EvidenceQuality.LOW
    assert item.origins[1].effective_date == date(2026, 7, 25)
    assert "future-dated evidence withheld" in item.origins[1].timing


def test_prompt_groups_exact_v1_bodies_without_rewriting_refs() -> None:
    first = EvidenceItem.create(
        source="source-a",
        evidence_type="filing",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="EXACT HISTORICAL BODY",
    )
    second = EvidenceItem.create(
        source="source-b",
        evidence_type="market",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="EXACT HISTORICAL BODY",
    )
    bundle = EvidenceBundle(
        version="1",
        instrument="7203.T",
        analysis_date=date(2026, 7, 24),
        items=(first, second),
    )

    index = _evidence_prompt_index(bundle)

    assert len(index) == 1
    assert index[0]["canonical_ref"] == first.ref
    assert index[0]["equivalent_refs"] == [first.ref, second.ref]
    assert [origin["source"] for origin in index[0]["origins"]] == [
        "source-a",
        "source-b",
    ]
    assert json.dumps(index).count("EXACT HISTORICAL BODY") == 1


def test_v1_digest_remains_compatible_when_origins_are_absent() -> None:
    item_payload = {
        "ref": "ev_0123456789ab",
        "source": "legacy",
        "evidence_type": "filing",
        "requested_date": "2026-07-24",
        "effective_date": "2026-07-24",
        "available_at": None,
        "content": "Legacy body.",
        "value": None,
        "unit": None,
        "quality": "high",
        "fallback": False,
        "provenance": {"timing": "point-in-time available"},
    }
    canonical = json.dumps(
        [item_payload],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()

    bundle = EvidenceBundle.model_validate(
        {
            "version": "1",
            "instrument": "7203.T",
            "analysis_date": "2026-07-24",
            "items": [item_payload],
            "sealed_at": "2026-07-24T12:00:00Z",
            "digest": digest,
        }
    )

    assert bundle.version == "1"
    assert bundle.digest == digest
    assert bundle.items[0].origins == ()


def test_markdown_export_renders_an_exact_body_once_with_all_refs() -> None:
    first = EvidenceItem.create(
        source="source-a",
        evidence_type="filing",
        requested_date=date(2026, 7, 24),
        content="ONE EXPORTED BODY",
    )
    second = EvidenceItem.create(
        source="source-b",
        evidence_type="market",
        requested_date=date(2026, 7, 24),
        content="ONE EXPORTED BODY",
    )
    evidence = EvidenceBundle(
        version="1",
        instrument="7203.T",
        analysis_date=date(2026, 7, 24),
        items=(first, second),
    )
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
    request = AnalysisRequest(ticker="7203.T", analysis_date="2026-07-24")
    run_export = RunExport(
        run=RunView(
            id="fixture-run",
            status=RunStatus.SUCCEEDED,
            request=request,
            config_snapshot={},
            attempt=1,
            cancel_requested=False,
            created_at=now,
            updated_at=now,
        ),
        result=AnalysisResult(
            run_id="fixture-run",
            status=RunStatus.SUCCEEDED,
            instrument="7203.T",
            reports={},
            decision=None,
            evidence=evidence,
        ),
        evidence=evidence,
    )

    markdown = render_run_export_markdown(run_export)

    assert markdown.count("ONE EXPORTED BODY") == 1
    assert f"`{first.ref}`" in markdown
    assert f"`{second.ref}`" in markdown
    assert "source-a, source-b" in markdown


def test_markdown_export_uses_canonical_report_order() -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
    result = AnalysisResult(
        run_id="fixture-run",
        status=RunStatus.SUCCEEDED,
        instrument="7203.T",
        reports={
            "social": "SOCIAL BODY",
            "news": "NEWS BODY",
            "market": "MARKET BODY",
            "fundamentals": "FUNDAMENTALS BODY",
        },
        decision=None,
    )
    run_export = RunExport(
        run=RunView(
            id="fixture-run",
            status=RunStatus.SUCCEEDED,
            request=AnalysisRequest(
                ticker="7203.T",
                analysis_date="2026-07-24",
            ),
            config_snapshot={},
            attempt=1,
            cancel_requested=False,
            created_at=now,
            updated_at=now,
        ),
        result=result,
    )

    markdown = render_run_export_markdown(run_export)

    assert list(result.reports) == [
        "fundamentals",
        "market",
        "news",
        "social",
    ]
    assert (
        markdown.index("FUNDAMENTALS BODY")
        < markdown.index("MARKET BODY")
        < markdown.index("NEWS BODY")
        < markdown.index("SOCIAL BODY")
    )
