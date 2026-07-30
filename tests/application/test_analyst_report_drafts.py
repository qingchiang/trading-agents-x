from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import (
    AnalystClaim,
    AnalystClaimType,
    EvidenceBundle,
    EvidenceItem,
    EvidenceTable,
    ResearchTableCell,
    ResearchTableColumn,
    ResearchTableRow,
    TableCellKind,
    TableDataType,
)
from tradingagents.graph.analyst_report_drafts import (
    AnalystReportDraft,
    AnalystSectionDraft,
    DerivedValueDraft,
    ResearchTableCellDraft,
    ResearchTableDraft,
    ResearchTablePlan,
    ResearchTableRowDraft,
    TableColumnDataType,
    TableColumnIntent,
    assemble_analyst_report,
)
from tradingagents.graph.output_validation import OutputValidationError


def _bundle() -> EvidenceBundle:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="financial comparison",
        requested_date=date(2026, 7, 30),
        effective_date=date(2026, 7, 30),
        content="Revenue was 100000 and the comparable value was 80000.",
    )
    columns = (
        ResearchTableColumn(
            key="metric",
            label="指标",
            data_type=TableDataType.TEXT,
        ),
        ResearchTableColumn(
            key="value",
            label="数值",
            data_type=TableDataType.NUMBER,
        ),
    )
    table = EvidenceTable.create(
        title="原始财务数据",
        purpose="用于核对报告中的观察值",
        columns=columns,
        rows=(
            ResearchTableRow(
                id="revenue",
                cells={
                    "metric": ResearchTableCell(
                        raw_value="收入",
                        display_value="收入",
                        kind=TableCellKind.OBSERVATION,
                        evidence_refs=(item.ref,),
                    ),
                    "value": ResearchTableCell(
                        raw_value=100_000,
                        display_value="100,000",
                        kind=TableCellKind.OBSERVATION,
                        evidence_refs=(item.ref,),
                    ),
                },
                evidence_refs=(item.ref,),
            ),
        ),
        evidence_refs=(item.ref,),
        source_format="structured",
    )
    return EvidenceBundle(
        instrument="4568.T",
        analysis_date=date(2026, 7, 30),
        items=(item,),
        tables=(table,),
    )


def _report_draft(
    bundle: EvidenceBundle,
    plan: ResearchTablePlan,
) -> AnalystReportDraft:
    ref = bundle.items[0].ref
    return AnalystReportDraft(
        analyst="fundamentals",
        executive_summary="収益力は改善しているが、継続性の確認が必要です。",
        confidence=0.7,
        claims=(
            AnalystClaim(
                id="fundamentals.claim_1",
                kind=AnalystClaimType.OBSERVATION,
                statement="收入为 100000。",
                implication="需要结合增长率判断质量。",
                confidence=0.8,
                evidence_refs=(ref,),
            ),
        ),
        sections=(
            AnalystSectionDraft(
                id="growth",
                title="增长",
                narrative=f"收入数据可由 {ref} 核对。",
                evidence_table_ids=(bundle.tables[0].id,),
                research_table_plans=(plan,),
            ),
        ),
        catalysts=(),
        risks=("可比口径可能发生变化。",),
        invalidation_conditions=("后续披露否定当前增长趋势。",),
        evidence_refs=(ref,),
    )


def test_table_draft_schema_excludes_public_mechanical_fields() -> None:
    schema = json.dumps(
        ResearchTableDraft.model_json_schema(),
        sort_keys=True,
    )

    assert "display_value" not in schema
    assert "source_evidence_table_id" not in schema
    assert "source_evidence_row_ids" not in schema
    assert '"result"' not in schema
    assert '"id"' not in schema


def test_percent_is_the_only_percentage_wire_value() -> None:
    assert TableDataType.PERCENT.value == "percent"
    assert TableColumnDataType.PERCENT.value == "percent"
    with pytest.raises(ValueError):
        TableDataType("percentage")
    with pytest.raises(ValidationError):
        TableColumnIntent(
            label="变化",
            data_type="percentage",
        )


def test_assembler_materializes_ids_display_values_and_formula_results() -> None:
    bundle = _bundle()
    ref = bundle.items[0].ref
    plan = ResearchTablePlan(
        title="收入与隐含增幅",
        purpose="比较当前收入并复算增长率",
        comparison_target="当前值与可比值",
        evidence_refs=(ref,),
        evidence_table_ids=(bundle.tables[0].id,),
        expected_columns=("指标", "金额", "变化"),
    )
    table_draft = ResearchTableDraft(
        columns=(
            TableColumnIntent(
                label="指标",
                data_type=TableColumnDataType.TEXT,
            ),
            TableColumnIntent(
                label="金额",
                data_type=TableColumnDataType.CURRENCY,
                compact=True,
                scale=10_000,
                fraction_digits=1,
                unit="CNY",
                unit_label="万元",
            ),
            TableColumnIntent(
                label="变化",
                data_type=TableColumnDataType.PERCENT,
                fraction_digits=1,
            ),
        ),
        rows=(
            ResearchTableRowDraft(
                cells=(
                    ResearchTableCellDraft(
                        raw_value="收入",
                        kind=TableCellKind.DESCRIPTOR,
                    ),
                    ResearchTableCellDraft(
                        raw_value=100_000,
                        evidence_refs=(ref,),
                    ),
                    ResearchTableCellDraft(
                        kind=TableCellKind.DERIVED,
                        evidence_refs=(ref,),
                        derivation=DerivedValueDraft(
                            formula="(current - prior) / prior",
                            inputs={"current": 100_000, "prior": 80_000},
                            input_evidence_refs=(ref,),
                            unit="ratio",
                        ),
                    ),
                ),
            ),
        ),
        evidence_refs=(ref,),
    )

    report = assemble_analyst_report(
        _report_draft(bundle, plan),
        (table_draft,),
        bundle=bundle,
        output_language="zh-CN",
    )

    table = report.tables[0]
    row = table.rows[0]
    assert table.id == "rt_fundamentals_growth_1"
    assert row.id == "rt_fundamentals_growth_1.row_1"
    assert report.sections[0].research_table_ids == (table.id,)
    assert report.sections[0].evidence_table_ids == (bundle.tables[0].id,)
    assert row.cells["column_2"].display_value == "¥10.0万元"
    assert row.cells["column_3"].raw_value == 0.25
    assert row.cells["column_3"].display_value == "25.0%"
    assert row.cells["column_3"].derived is not None
    assert row.cells["column_3"].derived.result == 0.25
    assert table.source_evidence_table_id is None


def test_assembler_links_only_an_exact_source_table_view() -> None:
    bundle = _bundle()
    ref = bundle.items[0].ref
    plan = ResearchTablePlan(
        title="收入核对",
        purpose="展示与原始事实表完全一致的观察值",
        comparison_target="原始披露值",
        evidence_refs=(ref,),
        evidence_table_ids=(bundle.tables[0].id,),
        expected_columns=("指标", "数值"),
    )
    table_draft = ResearchTableDraft(
        columns=(
            TableColumnIntent(
                label="指标",
                data_type=TableColumnDataType.TEXT,
            ),
            TableColumnIntent(
                label="数值",
                data_type=TableColumnDataType.NUMBER,
                fraction_digits=0,
            ),
        ),
        rows=(
            ResearchTableRowDraft(
                cells=(
                    ResearchTableCellDraft(
                        raw_value="收入",
                        kind=TableCellKind.OBSERVATION,
                    ),
                    ResearchTableCellDraft(raw_value=100_000),
                ),
            ),
        ),
        evidence_refs=(ref,),
    )

    table = assemble_analyst_report(
        _report_draft(bundle, plan),
        (table_draft,),
        bundle=bundle,
        output_language="zh-CN",
    ).tables[0]

    assert table.source_evidence_table_id == bundle.tables[0].id
    assert table.source_evidence_row_ids == ("revenue",)
    assert table.total_source_rows == 1


def test_assembler_rejects_4568_style_component_errors() -> None:
    bundle = _bundle()
    ref = bundle.items[0].ref
    plan = ResearchTablePlan(
        title="错误 fixture",
        purpose="验证组件错误不会进入公开报告",
        comparison_target="fixture",
        evidence_refs=(ref,),
        expected_columns=("指标", "隐含上行"),
    )
    report = _report_draft(bundle, plan)
    mismatched_columns = ResearchTableDraft(
        columns=(
            TableColumnIntent(
                label="指标",
                data_type=TableColumnDataType.TEXT,
            ),
            TableColumnIntent(
                label="错误列",
                data_type=TableColumnDataType.PERCENT,
            ),
        ),
        rows=(
            ResearchTableRowDraft(
                cells=(
                    ResearchTableCellDraft(
                        raw_value="目标价",
                        kind=TableCellKind.DESCRIPTOR,
                    ),
                    ResearchTableCellDraft(
                        raw_value=0.2,
                        evidence_refs=(ref,),
                    ),
                )
            ),
        ),
        evidence_refs=(ref,),
    )

    with pytest.raises(
        OutputValidationError,
        match="columns.plan_mismatch",
    ):
        assemble_analyst_report(
            report,
            (mismatched_columns,),
            bundle=bundle,
            output_language="zh-CN",
        )

    with pytest.raises(ValidationError, match="derived inputs must be numeric"):
        DerivedValueDraft(
            formula="target / current - 1",
            inputs={"target": True, "current": 100},
            input_evidence_refs=(ref,),
        )

    with pytest.raises(
        ValidationError,
        match="calculated by the application",
    ):
        ResearchTableCellDraft(
            raw_value=0.2,
            kind=TableCellKind.DERIVED,
            evidence_refs=(ref,),
            derivation=DerivedValueDraft(
                formula="target / current - 1",
                inputs={"target": 120, "current": 100},
                input_evidence_refs=(ref,),
            ),
        )
