"""Render self-contained, explicit run exports from durable contracts."""

from __future__ import annotations

import json

from .contracts import (
    AnalystReport,
    EvidenceTable,
    PerspectiveReview,
    ResearchDecision,
    ResearchTable,
    ResearchWarning,
    RunExport,
)
from .evidence import group_evidence_by_content


def render_run_export_markdown(run_export: RunExport) -> str:
    """Render a human-readable audit document without hidden model messages."""
    result = run_export.result
    process_artifacts = tuple(
        artifact
        for artifact in run_export.artifacts
        if artifact.stage not in {"analyst", "decision"}
    )
    sections = [
        f"# TradingAgentsX Research: {result.instrument}",
        "",
        f"- Export schema: `{run_export.schema_version}`",
        f"- Run: `{result.run_id}`",
        f"- Status: `{result.status.value}`",
        f"- Attempt: `{run_export.run.attempt}`",
        "",
        "## Research Process",
    ]
    if not process_artifacts:
        sections.extend(
            [
                "",
                "_No deliberation artifacts were recorded for this run._",
            ]
        )
    for artifact in process_artifacts:
        sections.extend(
            [
                "",
                (
                    f"### {artifact.stage} · {artifact.role} · "
                    f"round {artifact.round}"
                ),
                "",
                f"- Artifact: `{artifact.id}`",
                f"- Attempt: `{artifact.attempt}`",
                f"- Schema: `{artifact.schema_version}`",
                f"- Prompt: `{artifact.prompt_version}`",
                f"- Generation: `{artifact.generation_method.value}`",
                f"- Created: `{artifact.created_at.isoformat()}`",
            ]
        )
        human_text = _artifact_human_text(artifact.content)
        if human_text:
            sections.extend(["", human_text])
        sections.extend(
            [
                "",
                "```json",
                json.dumps(
                    artifact.content.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ]
        )

    sections.extend(["", "## Reports"])
    if not result.reports:
        sections.extend(["", "_No final reports were recorded._"])
    for name, report in result.reports.items():
        narrative = (
            _render_analyst_report(
                report,
                evidence_tables=(
                    {
                        table.id: table
                        for table in run_export.evidence.tables
                    }
                    if run_export.evidence is not None
                    else {}
                ),
            )
            if isinstance(report, AnalystReport)
            else str(report)
        )
        sections.extend(
            [
                "",
                f"### {name.title()}",
                "",
                narrative,
            ]
        )

    sections.extend(["", "## Research Decision"])
    if result.decision is None:
        sections.extend(["", "_No final decision was recorded._"])
    else:
        sections.extend(
            [
                "",
                result.decision.thesis,
                "",
                "```json",
                json.dumps(
                    result.decision.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ]
        )

    warnings = _export_warnings(run_export)
    sections.extend(["", "## Warnings"])
    if not warnings:
        sections.extend(["", "_No structured warnings were recorded._"])
    else:
        for warning in warnings:
            details = []
            if warning.source:
                details.append(f"source: {warning.source}")
            if warning.evidence_ref:
                details.append(f"evidence: `{warning.evidence_ref}`")
            suffix = f" ({'; '.join(details)})" if details else ""
            sections.append(
                f"- **{warning.code}**: {warning.message}{suffix}"
            )

    metrics = result.metrics
    sections.extend(
        [
            "",
            "## Performance",
            "",
            f"- LLM calls: `{metrics.llm_calls}`",
            f"- Tool calls: `{metrics.tool_calls}`",
            f"- Input tokens: `{metrics.input_tokens}`",
            f"- Output tokens: `{metrics.output_tokens}`",
            f"- Wall time: `{metrics.wall_time_seconds:.3f}s`",
        ]
    )
    node_names = set(metrics.node_metrics)
    if node_names:
        sections.extend(
            [
                "",
                "| Node | LLM calls | Tool calls | Input tokens | "
                "Output tokens | Wall time |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for node in sorted(
            node_names,
            key=lambda name: (
                -metrics.node_metrics[name].wall_time_seconds,
                name,
            ),
        ):
            node_usage = metrics.node_metrics[node]
            sections.append(
                f"| `{node}` | {node_usage.llm_calls} | "
                f"{node_usage.tool_calls} | {node_usage.input_tokens} | "
                f"{node_usage.output_tokens} | "
                f"{node_usage.wall_time_seconds:.3f}s |"
            )

    sections.extend(["", "## Evidence Appendix"])
    if run_export.evidence is None:
        sections.extend(
            ["", "_No sealed EvidenceBundle was recorded for this run._"]
        )
    else:
        sections.extend(
            [
                "",
                f"- Bundle version: `{run_export.evidence.version}`",
                f"- Digest: `{run_export.evidence.digest}`",
                f"- Analysis date: `{run_export.evidence.analysis_date}`",
            ]
        )
        if run_export.evidence.tables:
            sections.extend(["", "### Complete Evidence Tables"])
            for table in run_export.evidence.tables:
                sections.extend(["", *_render_evidence_table(table)])
            sections.extend(["", "### Evidence Items"])
        for group in group_evidence_by_content(run_export.evidence.items):
            item = group.canonical
            sources = tuple(
                dict.fromkeys(
                    origin.source
                    for grouped_item in group.items
                    for origin in grouped_item.origins
                )
            ) or tuple(
                dict.fromkeys(
                    grouped_item.source for grouped_item in group.items
                )
            )
            sections.extend(
                [
                    "",
                    f"### `{item.ref}`",
                    "",
                    "- Refs: "
                    + ", ".join(f"`{ref}`" for ref in group.refs),
                    f"- Sources: {', '.join(sources)}",
                    f"- Type: {item.evidence_type}",
                    f"- Quality: `{item.quality.value}`",
                    f"- Fallback: `{str(item.fallback).lower()}`",
                ]
            )
            if group.content:
                sections.extend(
                    [
                        "",
                        "#### Content",
                        "",
                        group.content,
                    ]
                )
            sections.extend(
                [
                    "",
                    "#### Audit records",
                    "",
                    "```json",
                    json.dumps(
                        [
                            grouped_item.model_dump(
                                mode="json",
                                exclude={"content"},
                            )
                            for grouped_item in group.items
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                ]
            )
    return "\n".join(sections)


def _render_evidence_table(table: EvidenceTable) -> list[str]:
    """Render every persisted row; exports never inherit Web pagination."""

    refs = ", ".join(f"`{ref}`" for ref in table.evidence_refs)
    headers = "| " + " | ".join(
        _escape_table_cell(column.label) for column in table.columns
    ) + " |"
    divider = "|" + "|".join("---" for _column in table.columns) + "|"
    rows = [
        "| "
        + " | ".join(
            _escape_table_cell(row.cells[column.key].display_value)
            for column in table.columns
        )
        + " |"
        for row in table.rows
    ]
    return [
        f"#### {table.title}",
        "",
        f"- Table: `{table.id}`",
        f"- Purpose: {table.purpose}",
        f"- Source format: `{table.source_format}`",
        f"- Evidence: {refs}",
        f"- Rows: `{len(table.rows)}` (complete)",
        "",
        headers,
        divider,
        *rows,
    ]


def _escape_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _render_research_table(table: ResearchTable) -> list[str]:
    headers = "| " + " | ".join(
        _escape_table_cell(column.label) for column in table.columns
    ) + " |"
    divider = "|" + "|".join("---" for _column in table.columns) + "|"
    rows = [
        "| "
        + " | ".join(
            _escape_table_cell(row.cells[column.key].display_value)
            for column in table.columns
        )
        + " |"
        for row in table.rows
    ]
    source_note = (
        (
            f"- Source view: `{len(table.rows)}/{table.total_source_rows}` "
            f"rows from `{table.source_table_id}`"
        )
        if table.source_table_id is not None
        else "- Source view: synthesized comparison"
    )
    return [
        f"##### {table.title}",
        "",
        f"- Table: `{table.id}`",
        f"- Purpose: {table.purpose}",
        source_note,
        "",
        headers,
        divider,
        *rows,
    ]


def _render_analyst_report(
    report: AnalystReport,
    *,
    evidence_tables: dict[str, EvidenceTable],
) -> str:
    research_tables = {table.id: table for table in report.tables}
    lines = [
        "#### Executive Summary",
        "",
        report.executive_summary,
    ]
    for section in report.sections:
        lines.extend(["", f"#### {section.title}", "", section.narrative])
        for table_id in section.table_ids:
            if table_id in evidence_tables:
                lines.extend(
                    ["", *_render_evidence_table(evidence_tables[table_id])]
                )
            elif table_id in research_tables:
                lines.extend(
                    ["", *_render_research_table(research_tables[table_id])]
                )
    lines.extend(
        [
            "",
            "#### Catalysts",
            *(
                [f"- {item}" for item in report.catalysts]
                if report.catalysts
                else ["- None identified."]
            ),
            "",
            "#### Risks",
            *[f"- {item}" for item in report.risks],
            "",
            "#### Invalidation Conditions",
            *[f"- {item}" for item in report.invalidation_conditions],
        ]
    )
    return "\n".join(lines)


def _artifact_human_text(
    content: AnalystReport | PerspectiveReview | ResearchDecision,
) -> str:
    if isinstance(content, AnalystReport):
        return _render_analyst_report(content, evidence_tables={})
    return content.thesis


def _export_warnings(run_export: RunExport) -> tuple[ResearchWarning, ...]:
    """Collect each structured warning once across durable result/artifacts."""

    warnings = [
        *run_export.result.warnings,
        *(
            warning
            for report in run_export.result.reports.values()
            if isinstance(report, AnalystReport)
            for warning in report.warnings
        ),
        *(
            warning
            for artifact in run_export.artifacts
            if isinstance(artifact.content, AnalystReport)
            for warning in artifact.content.warnings
        ),
    ]
    return tuple(dict.fromkeys(warnings))
