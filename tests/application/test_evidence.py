"""Canonical evidence bundle and audit export coverage."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
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
    ArtifactGenerationObservation,
    AuditedRangeEndpoint,
    CalculationRecord,
    DecisionBrief,
    DecisionCalculationUse,
    DecisionNumericAuditAppendix,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    EvidenceTemporalScope,
    EvidenceValueLocator,
    MarketReferenceBasis,
    MarketReferenceLevel,
    MeasurementKind,
    NodeMetrics,
    NumericAuditAppendixStatus,
    NumericAuditComponentType,
    NumericAuditOmission,
    NumericAuditPhase,
    NumericAuditSnapshot,
    NumericAuditStatus,
    NumericCalculationStatus,
    NumericDisplayStatus,
    NumericRequirementCheck,
    ResearchArtifact,
    ResearchScenarioKind,
    ResearchWarning,
    RiskReviewAdjustment,
    RiskReviewDisposition,
    RunExport,
    RunMetrics,
    RunStatus,
    RunView,
    ScenarioReferenceCategory,
    ScenarioReferenceRange,
    TableDataType,
    ValuationAssessment,
)
from tradingagents.application.evidence import extract_evidence_tables
from tradingagents.application.exporting import (
    render_run_export_markdown,
    render_run_export_package,
)
from tradingagents.graph.evidence_context import build_evidence_catalog
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

    index = build_evidence_catalog(bundle)["items"]

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

    assert bundle.version == "8"
    assert bundle.digest == digest
    with pytest.raises(ValidationError, match="Input should be '8'"):
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
        not cell.source_refs
        for row in csv_table.rows
        for cell in row.cells.values()
    )
    assert csv_table.evidence_refs == (first.ref, second.ref)


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
    assert change_cell.raw_value is None
    assert value_cell.source_refs == ()
    assert table.evidence_refs == (item.ref,)


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


def test_failed_run_export_preserves_non_final_decision_brief() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="market",
        requested_date=date(2026, 7, 24),
        content="Sealed evidence.",
    )
    evidence = EvidenceBundle(
        instrument="3778.T",
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
    run_export = RunExport(
        run=RunView(
            id="fixture-run",
            status=RunStatus.FAILED,
            request=AnalysisRequest(ticker="3778.T", analysis_date="2026-07-24"),
            config_snapshot={},
            attempt=1,
            cancel_requested=False,
            created_at=now,
            updated_at=now,
        ),
        result=AnalysisResult(
            run_id="fixture-run",
            status=RunStatus.FAILED,
            instrument="3778.T",
            reports={},
            decision=None,
            evidence=evidence,
        ),
        evidence=evidence,
        artifacts=(
            ResearchArtifact(
                id="artifact-brief",
                run_id="fixture-run",
                attempt=1,
                stage="decision_brief",
                role="final_committee",
                schema_version="2",
                prompt_version="final-committee-brief-v1",
                generation_method=ArtifactGenerationMethod.MARKDOWN_AUDITED,
                generation_observations=(
                    ArtifactGenerationObservation(
                        node="committee.final.reason",
                        task_kind="semantic_structured",
                        client_role="deep_reasoning",
                        generation_method=ArtifactGenerationMethod.JSON_MODE,
                    ),
                ),
                content=DecisionBrief(
                    markdown=f"# Draft\n\nNon-final synthesis.[^{item.ref}]",
                    evidence_refs=(item.ref,),
                ),
                created_at=now,
            ),
        ),
    )

    markdown = render_run_export_markdown(run_export)

    assert run_export.schema_version == "9"
    assert "Decision Synthesis Brief" in markdown
    assert "Non-final reasoning draft" in markdown
    assert "Non-final synthesis.[E01]" in markdown
    assert (
        "Generation path: `committee.final.reason` · `semantic_structured` · "
        "`deep_reasoning` · `json_mode`"
    ) in markdown
    assert "No final decision was recorded." in markdown


def test_markdown_export_uses_stable_evidence_markers_without_definitions() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="filing",
        requested_date=date(2026, 7, 24),
        content="Canonical ledger content.",
    )
    second = EvidenceItem.create(
        source="fixture-2",
        evidence_type="market",
        requested_date=date(2026, 7, 24),
        content="Second ledger content.",
    )
    evidence = EvidenceBundle(
        instrument="7203.T",
        analysis_date=date(2026, 7, 24),
        items=(item, second),
    )
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
    report = analyst_report(
        evidence_ref=item.ref,
        narrative=(
            f"# Report\n\nSupported finding.[^{item.ref}][^{second.ref}]\n\n"
            f"[^{item.ref}]: Model-authored source text."
        ),
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
        result=AnalysisResult(
            run_id="fixture-run",
            status=RunStatus.SUCCEEDED,
            instrument="7203.T",
            reports={"market": report},
            decision=None,
            evidence=evidence,
        ),
        evidence=evidence,
    )

    markdown = render_run_export_markdown(run_export)

    assert "Supported finding.[E01] [E02]" in markdown
    assert "Model-authored source text" not in markdown
    assert "### E01" in markdown
    assert f"- Refs: `{item.ref}`" in markdown


def test_markdown_export_links_raw_evidence_table_without_inlining_rows() -> None:
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

    assert "### Raw Evidence Tables" in markdown
    assert f"- Table: `{tables[0].id}`" in markdown
    assert "- Rows: `2`" in markdown
    assert f"`tables/{tables[0].id}.csv`" in markdown
    assert "| 2026-07-23 | 100.0 |" not in markdown
    assert "| 2026-07-24 | 101.5 |" not in markdown

    package = render_run_export_package(run_export)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        names = set(archive.namelist())
        expected = {
            "report.md",
            "run.json",
            "artifacts.json",
            "evidence.json",
            f"tables/{tables[0].id}.csv",
            "manifest.json",
        }
        assert names == expected
        csv_body = archive.read(f"tables/{tables[0].id}.csv").decode()
        assert "row_id,date,close" in csv_body
        assert f"{tables[0].rows[0].id},2026-07-23,100.0" in csv_body
        assert f"{tables[0].rows[1].id},2026-07-24,101.5" in csv_body
        manifest = json.loads(archive.read("manifest.json"))
        listed = {item["path"]: item for item in manifest["files"]}
        assert set(listed) == expected - {"manifest.json"}
        for path, entry in listed.items():
            content = archive.read(path)
            assert entry["size"] == len(content)
            assert entry["sha256"] == hashlib.sha256(content).hexdigest()


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


def test_markdown_export_renders_decision_calculation_uses_and_gap_only_appendix() -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)
    decision = research_decision(thesis="Forward PE is 82.1x.").model_copy(
        update={
            "calculation_records": (
                CalculationRecord(
                    id="calc_guidance_pe",
                    formula="price / eps",
                    inputs={"price": 3075, "eps": 37.46},
                    input_evidence_refs=("ev_0123456789ab",),
                    result=3075 / 37.46,
                    unit="x",
                    as_of_date=date(2026, 7, 24),
                    limitations=("Guidance may change.",),
                    decision_uses=(
                        DecisionCalculationUse(
                            component_path="thesis",
                            label="Forward PE",
                        ),
                    ),
                ),
            ),
            "numeric_audit_status": NumericAuditStatus.PARTIAL,
        }
    )
    run_export = RunExport(
        run=RunView(
            id="fixture-run",
            status=RunStatus.SUCCEEDED,
            request=AnalysisRequest(ticker="3778.T", analysis_date="2026-07-24"),
            config_snapshot={},
            attempt=1,
            cancel_requested=False,
            created_at=now,
            updated_at=now,
        ),
        result=AnalysisResult(
            run_id="fixture-run",
            status=RunStatus.SUCCEEDED,
            instrument="3778.T",
            reports={},
            decision=decision,
            numeric_audit=DecisionNumericAuditAppendix(
                status=NumericAuditAppendixStatus.PARTIAL,
                requirement_checks=(
                    NumericRequirementCheck(
                        requirement_id="req_forward_pe",
                        calculation_id="calc_forward_pe",
                        component_path="thesis",
                        label="Forward PE",
                        stated_value=45.8,
                        fraction_digits=1,
                        unit="x",
                        formula="price / eps",
                        inputs={"price": 3075, "eps": 37.46},
                        input_evidence_refs=("ev_0123456789ab",),
                        canonical_result=3075 / 37.46,
                        comparison_result=3075 / 37.46,
                        comparison_difference=(3075 / 37.46) - 45.8,
                        rounded_stated_value=45.8,
                        rounded_canonical_result=82.1,
                        calculation_status=NumericCalculationStatus.VERIFIED,
                        display_status=NumericDisplayStatus.MISMATCHED,
                        issue_codes=(
                            "numeric.requirement.req_forward_pe.result_mismatch",
                        ),
                    ),
                ),
                snapshots=(),
                omitted_components=(
                    NumericAuditOmission(
                        component_path="risks.0",
                        component_type=NumericAuditComponentType.DECISION_CLAIM,
                        reference_label="Remaining EPS",
                        issue_codes=(
                            "numeric.requirement.req_eps_remaining.missing_calculation",
                        ),
                    ),
                ),
            ),
        ),
    )

    markdown = render_run_export_markdown(run_export)

    assert "Thesis: Forward PE" in markdown
    assert "## Decision-Critical Calculation Audit" in markdown
    assert "Structured display value" in markdown
    assert "45.8 x" in markdown
    assert "82.09 x" in markdown
    assert "`mismatched`" in markdown
    assert "Decision-critical derived value · Remaining EPS" in markdown
    assert "Candidate was not parseable" not in markdown


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
    assert (
        "| `committee.final` | 1 | 0 | 300 | 0 | 0 | 100 | 0 | 0 | "
        "4.000s |"
    ) in markdown
    assert (
        "| `analyst.market` | 2 | 2 | 900 | 0 | 0 | 200 | 0 | 0 | "
        "2.500s |"
    ) in markdown
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
                low=AuditedRangeEndpoint(
                    value=100,
                    basis=MarketReferenceBasis.DERIVED,
                    evidence_refs=("ev_0123456789ab",),
                    date_evidence_refs=("ev_0123456789ab",),
                    calculation_id="calc_valuation_low",
                    as_of_date=date(2026, 7, 24),
                ),
                high=AuditedRangeEndpoint(
                    value=125,
                    basis=MarketReferenceBasis.DERIVED,
                    evidence_refs=("ev_0123456789ab",),
                    date_evidence_refs=("ev_0123456789ab",),
                    calculation_id="calc_valuation_high",
                    as_of_date=date(2026, 7, 24),
                ),
                measurement_kind=MeasurementKind.CURRENCY,
                unit="USD",
                limitations=("Cycle duration remains uncertain.",),
            ),
            "market_reference_levels": (
                MarketReferenceLevel(
                    label="Recent support",
                    value=98,
                    unit="USD",
                    as_of_date=date(2026, 7, 24),
                    interpretation="Observation only, not an entry order.",
                    evidence_refs=("ev_0123456789ab",),
                    date_evidence_refs=("ev_0123456789ab",),
                    source_locator=EvidenceValueLocator(
                        evidence_ref="ev_0123456789ab"
                    ),
                ),
                MarketReferenceLevel(
                    label="Unclassified signal",
                    value=7.25,
                    measurement_kind=MeasurementKind.UNKNOWN,
                    unit=None,
                    as_of_date=date(2026, 7, 24),
                    interpretation="The source did not publish a unit.",
                    evidence_refs=("ev_0123456789ab",),
                    date_evidence_refs=("ev_0123456789ab",),
                    basis=MarketReferenceBasis.INTERPRETED,
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
            "numeric_audit_status": NumericAuditStatus.COMPLETE,
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
            numeric_audit=DecisionNumericAuditAppendix(
                status=NumericAuditAppendixStatus.PARTIAL,
                snapshots=(
                    NumericAuditSnapshot(
                        phase=NumericAuditPhase.INITIAL,
                        method=ArtifactGenerationMethod.TOOL_CALL,
                        reason_code="semantic_validation",
                        validation_issues=(
                            "semantic.numeric.calculation.calc_valuation.formula.invalid_syntax",
                        ),
                        schema_valid=True,
                        candidate={
                            "requested": True,
                            "calculation_records": [{"id": "calc_valuation"}],
                        },
                        candidate_digest="b" * 64,
                    ),
                ),
                omitted_components=(
                    NumericAuditOmission(
                        component_path="numeric.calculation.calc_valuation",
                        component_type=NumericAuditComponentType.CALCULATION,
                        reference_label="calc_valuation",
                        issue_codes=(
                            "numeric.calculation.calc_valuation.formula.invalid_syntax",
                        ),
                    ),
                ),
            ),
            warnings=(warning,),
        ),
        artifacts=artifacts,
    )

    markdown = render_run_export_markdown(run_export)

    assert "unit unspecified" not in markdown

    assert markdown.count("## Research Process") == 1
    assert markdown.count("## Reports") == 1
    assert markdown.count("## Research Decision") == 1
    assert markdown.count("## Warnings") == 1
    assert markdown.count("## Performance") == 1
    assert markdown.count("## Sources") == 1
    assert markdown.count("MODEL REPORT") == 1
    assert markdown.count("Historical coverage is partial.") == 1
    assert "review-artifact" in markdown
    assert "analyst-artifact" not in markdown
    assert "decision-artifact" not in markdown
    assert "#### Key Claim Audit" in markdown
    assert "Fixture case statement" in markdown
    assert "Non-personalized research opinion" in markdown
    assert "### Scenarios" in markdown
    assert "### Valuation Assessment" in markdown
    assert "- Numeric audit: `complete`" in markdown
    assert "Scenario-weighted multiple" in markdown
    assert "### Market Reference Levels" in markdown
    assert "Observation only, not an entry order." in markdown
    assert "### Final Committee Response to Risk Review" in markdown
    assert "Confidence calibration" in markdown
    assert markdown.count("## Unverified Numeric Drafts") == 1
    assert "calc_valuation" in markdown
    package = render_run_export_package(run_export)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        exported = json.loads(archive.read("run.json"))
    assert exported["result"]["numeric_audit"]["status"] == "partial"
    assert markdown.index("## Reports") < markdown.index("## Research Process")
    assert markdown.index("## Research Process") < markdown.index("## Research Decision")


@pytest.mark.parametrize(
    ("output_language", "expected"),
    (
        ("en", ("## Reports", "## Research Decision", "## Warnings", "## Sources")),
        ("zh-CN", ("## 研究报告", "## 最终结论", "## 警告", "## 来源")),
        ("ja", ("## リサーチレポート", "## 最終結論", "## 警告", "## 情報源")),
        (
            "使用正式、克制的繁体中文",
            ("## Reports", "## Research Decision", "## Warnings", "## Sources"),
        ),
    ),
)
def test_export_framework_uses_standard_locales_and_custom_language_fallback(
    output_language: str,
    expected: tuple[str, str, str, str],
) -> None:
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    run_export = RunExport(
        run=RunView(
            id="fixture-run",
            status=RunStatus.FAILED,
            request=AnalysisRequest(
                ticker="6501.T",
                analysis_date="2026-08-01",
                output_language=output_language,
            ),
            config_snapshot={},
            attempt=1,
            cancel_requested=False,
            created_at=now,
            updated_at=now,
        ),
        result=AnalysisResult(
            run_id="fixture-run",
            status=RunStatus.FAILED,
            instrument="6501.T",
            reports={},
            decision=None,
        ),
    )

    markdown = render_run_export_markdown(run_export)

    assert all(heading in markdown for heading in expected)
    with zipfile.ZipFile(io.BytesIO(render_run_export_package(run_export))) as archive:
        assert archive.read("report.md").decode() == markdown


@pytest.mark.parametrize(
    ("output_language", "category_label", "omission_label"),
    (
        ("en", "Analyst consensus", "Base · Scenario reference range"),
        ("zh-CN", "卖方共识", "基准情景 · 情景参考区间"),
        ("ja", "アナリスト予想", "基本シナリオ · シナリオ参考レンジ"),
    ),
)
def test_export_localizes_scenario_range_categories_and_omissions(
    output_language: str,
    category_label: str,
    omission_label: str,
) -> None:
    ref = "ev_0123456789ab"
    endpoint = AuditedRangeEndpoint(
        value=100,
        basis=MarketReferenceBasis.INTERPRETED,
        evidence_refs=(ref,),
        date_evidence_refs=(ref,),
        as_of_date=date(2026, 7, 24),
    )
    decision = research_decision(evidence_refs=(ref,))
    decision = decision.model_copy(
        update={
            "scenarios": tuple(
                scenario.model_copy(
                    update={
                        "reference_ranges": (
                            ScenarioReferenceRange(
                                category=ScenarioReferenceCategory.ANALYST_CONSENSUS,
                                label="Target range",
                                low=endpoint,
                                high=endpoint.model_copy(update={"value": 4199.4116}),
                                unit="JPY",
                                interpretation="Consensus reference.",
                                limitations=("Coverage may change.",),
                            ),
                        )
                    }
                )
                if scenario.kind is ResearchScenarioKind.BASE
                else scenario
                for scenario in decision.scenarios
            )
        }
    )
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    run_export = RunExport(
        run=RunView(
            id="fixture-run",
            status=RunStatus.SUCCEEDED,
            request=AnalysisRequest(
                ticker="6501.T",
                analysis_date="2026-08-01",
                output_language=output_language,
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
            instrument="6501.T",
            reports={},
            decision=decision,
            numeric_audit=DecisionNumericAuditAppendix(
                status=NumericAuditAppendixStatus.PARTIAL,
                snapshots=(
                    NumericAuditSnapshot(
                        phase=NumericAuditPhase.REPAIR,
                        method=ArtifactGenerationMethod.TOOL_CALL_RECOVERED,
                        reason_code="semantic_validation",
                        schema_valid=True,
                    ),
                ),
                omitted_components=(
                    NumericAuditOmission(
                        component_path="numeric.scenario.base.ranges.1",
                        component_type=NumericAuditComponentType.SCENARIO_RANGE,
                        scenario_kind=ResearchScenarioKind.BASE,
                        reference_label="Secondary target range",
                        issue_codes=("numeric.scenario.base.ranges.1.low.invalid",),
                    ),
                ),
            ),
        ),
    )

    markdown = render_run_export_markdown(run_export)

    assert category_label in markdown
    assert omission_label in markdown
    assert "Secondary target range" in markdown
    assert "`100`–`4,199.41` JPY" in markdown
    assert '"value":4199.4116' in run_export.model_dump_json()


def test_numeric_date_evidence_must_be_part_of_endpoint_evidence() -> None:
    with pytest.raises(ValueError, match="date evidence refs"):
        AuditedRangeEndpoint(
            value=100,
            basis=MarketReferenceBasis.INTERPRETED,
            evidence_refs=("ev_0123456789ab",),
            date_evidence_refs=("ev_ffffffffffff",),
            as_of_date=date(2026, 7, 24),
        )


def test_zh_export_localizes_framework_and_keeps_canonical_refs_in_sources() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="market snapshot",
        requested_date=date(2026, 8, 1),
        content="Sanitized market snapshot.",
    )
    evidence = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=(item,),
    )
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    decision = research_decision(evidence_refs=(item.ref,)).model_copy(
        update={"numeric_audit_status": NumericAuditStatus.INCOMPLETE}
    )
    warning = ResearchWarning(
        code="report.audit_incomplete",
        message="The readable report was preserved, but its audit is incomplete.",
        source="fundamentals analyst",
    )
    run_export = RunExport(
        run=RunView(
            id="fixture-run",
            status=RunStatus.SUCCEEDED,
            request=AnalysisRequest(
                ticker="6501.T",
                analysis_date="2026-08-01",
                output_language="zh-CN",
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
            instrument="6501.T",
            reports={
                "fundamentals": analyst_report(
                    evidence_ref=item.ref,
                    narrative=f"# 基本面\n\n已验证结论。[^{item.ref}]",
                )
            },
            decision=decision,
            evidence=evidence,
            numeric_audit=DecisionNumericAuditAppendix(
                status=NumericAuditAppendixStatus.INCOMPLETE,
                snapshots=(
                    NumericAuditSnapshot(
                        phase=NumericAuditPhase.INITIAL,
                        method=ArtifactGenerationMethod.TOOL_CALL,
                        reason_code="schema_validation",
                        validation_issues=(),
                        schema_valid=False,
                        candidate=None,
                        candidate_digest="c" * 64,
                    ),
                ),
            ),
            warnings=(warning,),
        ),
        evidence=evidence,
    )

    markdown = render_run_export_markdown(run_export)
    readable, sources = markdown.split("## 来源", maxsplit=1)

    assert "[E01]" in readable
    assert item.ref not in readable
    assert f"`{item.ref}`" in sources
    assert "### 初次候选" in readable
    assert "未记录校验问题" in readable
    assert "可读报告已保留，但关键观点审计不完整。" in readable
    assert "Evidence:" not in readable
    assert "Calculations:" not in readable
    assert "Candidate" not in readable
    assert "none recorded" not in readable
    assert markdown.count("## 来源") == 1
