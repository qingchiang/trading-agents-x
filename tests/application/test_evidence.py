"""Canonical evidence bundle and audit export coverage."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

import pytest
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from tests.factories import (
    analyst_report,
    research_case,
    research_decision,
)
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    ArtifactGenerationMethod,
    DerivedValue,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    EvidenceTemporalScope,
    MarketReferenceLevel,
    NodeMetrics,
    ResearchArtifact,
    ResearchTable,
    ResearchTableCell,
    ResearchTableColumn,
    ResearchTableRow,
    ResearchWarning,
    RiskReviewAdjustment,
    RiskReviewDisposition,
    RunExport,
    RunMetrics,
    RunStatus,
    RunView,
    TableCellKind,
    TableDataType,
    ValuationAssessment,
    ValuationRange,
)
from tradingagents.application.evidence import extract_evidence_tables
from tradingagents.application.exporting import render_run_export_markdown
from tradingagents.graph.deliberation import _evidence_payload
from tradingagents.graph.research_graph import (
    _collect_evidence,
    _evidence_from_records,
)
from tradingagents.provenance import (
    ProvenanceRecord,
    attach_evidence_span,
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


def test_complete_artifact_is_collected_instead_of_model_overview() -> None:
    record = _record("get_stock_data", "yfinance")
    artifact = {
        "schema_version": "1",
        "kind": "source",
        "dataset_id": "ds_0123456789ab",
        "evidence_type": "get_stock_data",
        "source_content": "Date,Close\n2026-07-24,123.45",
        "provenance": [
            {
                "evidence": record.evidence,
                "source": record.source,
                "requested": record.requested,
                "effective": record.effective,
                "timing": record.timing,
                "retrieved_at": record.retrieved_at,
            }
        ],
        "temporal_scope": "point_in_time",
        "analytical_views": {"row_count": 1},
    }

    items = _collect_evidence(
        [
            ToolMessage(
                content="MODEL-SAFE OVERVIEW",
                artifact=artifact,
                tool_call_id="fixture",
            )
        ],
        "",
        requested_date=date(2026, 7, 24),
        analyst="market",
    )

    assert len(items) == 1
    assert items[0].content == artifact["source_content"]
    assert "MODEL-SAFE OVERVIEW" not in items[0].content
    assert items[0].source == "yfinance"


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


def test_explicit_temporal_spans_split_composite_tool_content() -> None:
    pit = attach_evidence_span(
        attach_provenance(
            "DISCLOSURE-SAFE BODY",
            _record(
                "filing",
                "EDINET",
                timing="disclosure-date filtered",
            ),
        ),
        temporal_scope="point_in_time",
    )
    live = attach_evidence_span(
        attach_provenance(
            "RETRIEVAL SNAPSHOT BODY",
            _record(
                "analyst consensus",
                "yfinance",
                effective="retrieval-time snapshot",
                timing="live non-point-in-time",
            ),
        ),
        temporal_scope="live_only",
    )

    items = _collect_evidence(
        [ToolMessage(content=f"{pit}\n\n{live}", tool_call_id="fixture")],
        "Report.",
        requested_date=date(2026, 7, 24),
        analyst="fundamentals",
    )

    assert len(items) == 2
    by_scope = {item.origins[0].temporal_scope: item for item in items}
    assert by_scope[EvidenceTemporalScope.POINT_IN_TIME].content == "DISCLOSURE-SAFE BODY"
    live_item = by_scope[EvidenceTemporalScope.LIVE_ONLY]
    assert live_item.content == "RETRIEVAL SNAPSHOT BODY"
    assert live_item.quality is EvidenceQuality.LOW
    assert live_item.origins[0].retrieved_at == "2026-07-24T12:00:00Z"


def test_unavailable_live_span_keeps_audit_record_without_body() -> None:
    content = attach_evidence_span(
        attach_provenance(
            "Vendor was not queried.",
            _record(
                "analyst consensus",
                "yfinance",
                effective="—",
                timing=("live-only; unavailable for historical or future date; vendor not queried"),
            ),
        ),
        temporal_scope="live_only",
    )

    item = _collect_evidence(
        [ToolMessage(content=content, tool_call_id="fixture")],
        "Report.",
        requested_date=date(2026, 7, 24),
        analyst="fundamentals",
    )[0]

    assert item.content is None
    assert item.quality is EvidenceQuality.UNAVAILABLE
    assert item.origins[0].temporal_scope is EvidenceTemporalScope.LIVE_ONLY


def test_unbounded_mixed_temporal_content_fails_closed() -> None:
    content = attach_provenance(
        "UNSEPARATED BODY",
        _record("filing", "EDINET", timing="disclosure-date filtered"),
        _record(
            "analyst consensus",
            "yfinance",
            effective="retrieval-time snapshot",
            timing="live non-point-in-time",
        ),
    )

    item = _collect_evidence(
        [ToolMessage(content=content, tool_call_id="fixture")],
        "Report.",
        requested_date=date(2026, 7, 24),
        analyst="fundamentals",
    )[0]

    assert item.content is None
    assert item.provenance["mixed_temporal_scope_unseparated"] is True


def test_prompt_catalog_preserves_refs_without_serializing_exact_bodies() -> None:
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
        instrument="7203.T",
        analysis_date=date(2026, 7, 24),
        items=(first, second),
    )

    index = _evidence_payload(bundle)["items"]

    assert len(index) == 2
    assert [item["ref"] for item in index] == [first.ref, second.ref]
    assert all("content" not in item for item in index)
    assert all(item["content_characters"] == 21 for item in index)
    assert "EXACT HISTORICAL BODY" not in json.dumps(index)


def test_bundle_digest_covers_items_and_tables_without_legacy_versions() -> None:
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
        "origins": [],
        "provenance": {"timing": "point-in-time available"},
    }
    item = EvidenceItem.model_validate(item_payload)
    table = extract_evidence_tables(
        (
            item.model_copy(
                update={
                    "content": (
                        "## Filing comparison\n\n"
                        "| Metric | 2026 | 2025 |\n"
                        "|---|---:|---:|\n"
                        "| Revenue | 120 | 100 |"
                    )
                }
            ),
        )
    )[0]
    canonical = json.dumps(
        {
            "items": [item.model_dump(mode="json")],
            "tables": [table.model_dump(mode="json")],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    bundle = EvidenceBundle(
        instrument="7203.T",
        analysis_date=date(2026, 7, 24),
        items=(item,),
        tables=(table,),
        digest=digest,
    )

    assert bundle.version == "3"
    assert bundle.digest == digest
    with pytest.raises(ValidationError, match="Input should be '3'"):
        EvidenceBundle.model_validate(
            {
                **bundle.model_dump(mode="json"),
                "version": "2",
            }
        )


def test_exact_source_tables_are_extracted_once_without_row_limits() -> None:
    csv_rows = "\n".join(f"2026-05-{index:02d},{100 + index}.5" for index in range(1, 29))
    content = (
        "## Verified snapshot\n\n"
        "| Field | Value |\n"
        "|---|---:|\n"
        "| Close | 123.45 |\n"
        "| Volume | 1200000 |\n\n"
        "# Full price history\n"
        "Date,Close\n"
        f"{csv_rows}"
    )
    first = EvidenceItem.create(
        source="source-a",
        evidence_type="market data",
        requested_date=date(2026, 7, 24),
        content=content,
    )
    second = EvidenceItem.create(
        source="source-b",
        evidence_type="market data",
        requested_date=date(2026, 7, 24),
        content=content,
    )

    tables = extract_evidence_tables((first, second))

    assert len(tables) == 2
    markdown_table, csv_table = tables
    assert markdown_table.source_format == "markdown"
    assert markdown_table.evidence_refs == (first.ref, second.ref)
    assert markdown_table.rows[0].cells["value"].raw_value == 123.45
    assert csv_table.source_format == "csv"
    assert len(csv_table.rows) == 28
    assert csv_table.columns[0].data_type is TableDataType.DATE
    assert csv_table.columns[1].data_type is TableDataType.NUMBER
    assert all(
        cell.evidence_refs == (first.ref, second.ref)
        for row in csv_table.rows
        for cell in row.cells.values()
    )


def test_source_table_empty_cells_remain_explicit_missing_values() -> None:
    item = EvidenceItem.create(
        source="macro fixture",
        evidence_type="macro panel",
        requested_date=date(2026, 7, 29),
        content=(
            "## Global macro panel\n\n"
            "| Series | Value | Change |\n"
            "|---|---:|---:|\n"
            "| Rates |  |  |\n"
            "| Policy rate | 0.75 | — |"
        ),
    )

    table = extract_evidence_tables((item,))[0]
    category_row = table.rows[0]
    value_cell = category_row.cells["value"]
    change_cell = category_row.cells["change"]

    assert value_cell.raw_value is None
    assert value_cell.display_value == "—"
    assert change_cell.raw_value is None
    assert change_cell.display_value == "—"
    assert value_cell.evidence_refs == (item.ref,)


def test_table_contract_distinguishes_observed_and_derived_values() -> None:
    ref = "ev_0123456789ab"
    columns = (
        ResearchTableColumn(key="metric", label="Metric"),
        ResearchTableColumn(
            key="value",
            label="Value",
            data_type=TableDataType.PERCENTAGE,
            unit="%",
        ),
    )
    table = ResearchTable(
        id="rt_margin_change",
        title="Margin change",
        purpose="Show a reproducible period comparison.",
        columns=columns,
        rows=(
            ResearchTableRow(
                id="row_0001",
                cells={
                    "metric": ResearchTableCell(
                        raw_value="Operating margin change",
                        display_value="Operating margin change",
                        kind=TableCellKind.DESCRIPTOR,
                    ),
                    "value": ResearchTableCell(
                        raw_value=2.5,
                        display_value="+2.5 pp",
                        kind=TableCellKind.DERIVED,
                        evidence_refs=(ref,),
                        derived=DerivedValue(
                            formula="current_margin - prior_margin",
                            inputs={
                                "current_margin": 12.5,
                                "prior_margin": 10.0,
                            },
                            input_evidence_refs=(ref,),
                            unit="percentage points",
                            result=2.5,
                        ),
                    ),
                },
            ),
        ),
    )

    assert table.rows[0].cells["value"].derived.result == 2.5
    with pytest.raises(ValidationError, match="require evidence refs"):
        ResearchTableCell(
            raw_value=12.5,
            display_value="12.5%",
            kind=TableCellKind.OBSERVATION,
        )


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


def test_markdown_export_contains_every_evidence_table_row() -> None:
    item = EvidenceItem.create(
        source="verified fixture",
        evidence_type="market snapshot",
        requested_date=date(2026, 7, 24),
        content=(
            "## Recent closes\n\n"
            "| Date | Close |\n"
            "|---|---:|\n"
            "| 2026-07-23 | 100.0 |\n"
            "| 2026-07-24 | 101.5 |"
        ),
    )
    tables = extract_evidence_tables((item,))
    evidence = EvidenceBundle(
        instrument="7203.T",
        analysis_date=date(2026, 7, 24),
        items=(item,),
        tables=tables,
    )
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
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

    assert "### Complete Evidence Tables" in markdown
    assert f"- Table: `{tables[0].id}`" in markdown
    assert "- Rows: `2` (complete)" in markdown
    assert "| 2026-07-23 | 100.0 |" in markdown
    assert "| 2026-07-24 | 101.5 |" in markdown


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


def test_markdown_export_includes_total_and_per_node_metrics() -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
    metrics = RunMetrics(
        llm_calls=3,
        tool_calls=2,
        input_tokens=1200,
        output_tokens=300,
        wall_time_seconds=9.5,
        node_metrics={
            "analyst.market": NodeMetrics(
                llm_calls=2,
                tool_calls=2,
                input_tokens=900,
                output_tokens=200,
                wall_time_seconds=2.5,
            ),
            "committee.final": NodeMetrics(
                llm_calls=1,
                input_tokens=300,
                output_tokens=100,
                wall_time_seconds=4.0,
            ),
        },
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
            metrics=metrics,
            created_at=now,
            updated_at=now,
        ),
        result=AnalysisResult(
            run_id="fixture-run",
            status=RunStatus.SUCCEEDED,
            instrument="7203.T",
            reports={},
            decision=None,
            metrics=metrics,
        ),
    )

    markdown = render_run_export_markdown(run_export)

    assert "## Performance" in markdown
    assert "- Input tokens: `1200`" in markdown
    assert "| `committee.final` | 1 | 0 | 300 | 100 | 4.000s |" in markdown
    assert "| `analyst.market` | 2 | 2 | 900 | 200 | 2.500s |" in markdown
    assert markdown.index("committee.final") < markdown.index("analyst.market")


def test_markdown_export_emits_each_audit_section_once() -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
    warning = ResearchWarning(
        code="evidence.partial",
        message="Historical coverage is partial.",
        source="fixture",
    )
    narrative = "MODEL REPORT"
    report = analyst_report(
        executive_summary="Summary.",
        confidence=0.7,
        warnings=(warning,),
        narrative=narrative,
    )
    base_decision = research_decision(
        confidence=0.6,
        thesis="FINAL THESIS",
        risks=("Demand weakens.",),
        invalidation_conditions=("A new filing changes the evidence.",),
    )
    decision = base_decision.model_copy(
        update={
            "valuation_assessment": ValuationAssessment(
                method="Scenario-weighted multiple",
                valuation_range=ValuationRange(low=100, high=125),
                currency="USD",
                as_of_date=date(2026, 7, 24),
                input_evidence_refs=("ev_0123456789ab",),
                limitations=("Cycle duration remains uncertain.",),
            ),
            "market_reference_levels": (
                MarketReferenceLevel(
                    level_type="recent_support",
                    value=98,
                    unit="USD",
                    as_of_date=date(2026, 7, 24),
                    interpretation="Observation only, not an entry order.",
                    evidence_refs=("ev_0123456789ab",),
                ),
            ),
            "risk_review_adjustments": (
                RiskReviewAdjustment(
                    source_role="conservative",
                    disposition=RiskReviewDisposition.MODIFIED,
                    subject="Confidence calibration",
                    explanation="Confidence was reduced.",
                    evidence_refs=("ev_0123456789ab",),
                ),
            ),
        }
    )
    artifacts = (
        ResearchArtifact(
            id="analyst-artifact",
            run_id="fixture-run",
            attempt=1,
            stage="analyst",
            role="market",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=report,
            created_at=now,
        ),
        ResearchArtifact(
            id="review-artifact",
            run_id="fixture-run",
            attempt=1,
            stage="case",
            role="bear",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=research_case(role="bear"),
            created_at=now,
        ),
        ResearchArtifact(
            id="decision-artifact",
            run_id="fixture-run",
            attempt=1,
            stage="decision",
            role="final_committee",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=decision,
            created_at=now,
        ),
    )
    run_export = RunExport(
        run=RunView(
            id="fixture-run",
            status=RunStatus.SUCCEEDED,
            request=AnalysisRequest(
                ticker="NVDA",
                analysis_date="2026-07-24",
            ),
            config_snapshot={},
            attempt=1,
            cancel_requested=False,
            created_at=now,
            updated_at=now,
        ),
        result=AnalysisResult(
            run_id="fixture-run",
            status=RunStatus.SUCCEEDED,
            instrument="NVDA",
            reports={"market": report},
            decision=decision,
            warnings=(warning,),
        ),
        artifacts=artifacts,
    )

    markdown = render_run_export_markdown(run_export)

    assert markdown.count("## Research Process") == 1
    assert markdown.count("## Reports") == 1
    assert markdown.count("## Research Decision") == 1
    assert markdown.count("## Warnings") == 1
    assert markdown.count("## Performance") == 1
    assert markdown.count("## Evidence Appendix") == 1
    assert markdown.count("MODEL REPORT") == 1
    assert markdown.count("Historical coverage is partial.") == 1
    assert "review-artifact" in markdown
    assert "analyst-artifact" not in markdown
    assert "decision-artifact" not in markdown
    assert "#### Auditable Claims" in markdown
    assert "##### Arguments" in markdown
    assert "##### Strongest Counterarguments" in markdown
    assert "##### Fragile Assumptions" in markdown
    assert "Non-personalized research opinion" in markdown
    assert "### Scenarios" in markdown
    assert "### Valuation Assessment" in markdown
    assert "Scenario-weighted multiple" in markdown
    assert "### Market Reference Levels" in markdown
    assert "Observation only, not an entry order." in markdown
    assert "### Final Committee Response to Risk Review" in markdown
    assert "Confidence calibration" in markdown
    assert markdown.index("## Reports") < markdown.index("## Research Process")
    assert markdown.index("## Research Process") < markdown.index("## Research Decision")
