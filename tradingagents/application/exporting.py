"""Render self-contained, explicit run exports from durable contracts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from typing import Any

from .contracts import (
    AnalystReport,
    DebateAgenda,
    EvidenceTable,
    JudgeDraft,
    RebuttalReview,
    ResearchArtifactContent,
    ResearchCase,
    ResearchDecision,
    ResearchTable,
    ResearchTableColumn,
    ResearchTableRow,
    ResearchWarning,
    RiskReview,
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
        "## Reports",
    ]
    if not result.reports:
        sections.extend(["", "_No final reports were recorded._"])
    for name, report in result.reports.items():
        narrative = (
            _render_analyst_report(report) if isinstance(report, AnalystReport) else str(report)
        )
        sections.extend(
            [
                "",
                f"### {name.title()}",
                "",
                narrative,
            ]
        )

    sections.extend(["", "## Research Process"])
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
                (f"### {artifact.stage} · {artifact.role} · round {artifact.round}"),
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

    sections.extend(["", "## Research Decision"])
    if result.decision is None:
        sections.extend(["", "_No final decision was recorded._"])
    else:
        sections.extend(["", _render_research_decision(result.decision)])

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
            sections.append(f"- **{warning.code}**: {warning.message}{suffix}")

    metrics = result.metrics
    sections.extend(
        [
            "",
            "## Performance",
            "",
            (
                "_Usage is the cumulative amount observed and persisted by this "
                "application. A hard process crash can prevent the final provider "
                "callback from being recorded._"
            ),
            "",
            f"- LLM calls: `{metrics.llm_calls}`",
            f"- Tool calls: `{metrics.tool_calls}`",
            f"- Input tokens: `{metrics.input_tokens}`",
            f"- Output tokens: `{metrics.output_tokens}`",
            f"- Cache-hit input tokens: `{metrics.cache_hit_input_tokens}`",
            f"- Cache-miss input tokens: `{metrics.cache_miss_input_tokens}`",
            f"- Reasoning output tokens: `{metrics.reasoning_output_tokens}`",
            (
                "- Detailed usage coverage: "
                f"`{metrics.detailed_usage_calls}/{metrics.llm_calls}` calls"
            ),
            f"- Wall time: `{metrics.wall_time_seconds:.3f}s`",
        ]
    )
    node_names = set(metrics.node_metrics)
    if node_names:
        sections.extend(
            [
                "",
                "| Node | LLM calls | Tool calls | Input tokens | Cache hit | "
                "Cache miss | Output tokens | Reasoning | Detailed calls | Wall time |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
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
                f"{node_usage.cache_hit_input_tokens} | "
                f"{node_usage.cache_miss_input_tokens} | "
                f"{node_usage.output_tokens} | "
                f"{node_usage.reasoning_output_tokens} | "
                f"{node_usage.detailed_usage_calls} | "
                f"{node_usage.wall_time_seconds:.3f}s |"
            )

    sections.extend(["", "### Attempts"])
    if not run_export.attempts:
        sections.extend(["", "_No attempt metrics were recorded._"])
    else:
        sections.extend(
            [
                "",
                "| Attempt | Status | Resumes | Error | LLM calls | Tool calls | "
                "Input tokens | Output tokens | Wall time |",
                "|---:|---|---:|---|---:|---:|---:|---:|---:|",
            ]
        )
        for attempt in run_export.attempts:
            attempt_metrics = attempt.metrics
            sections.append(
                f"| {attempt.attempt} | {attempt.status.value} | "
                f"{attempt.resume_count} | {attempt.error_code or '—'} | "
                f"{attempt_metrics.llm_calls} | {attempt_metrics.tool_calls} | "
                f"{attempt_metrics.input_tokens} | "
                f"{attempt_metrics.output_tokens} | "
                f"{attempt_metrics.wall_time_seconds:.3f}s |"
            )

    sections.extend(["", "## Evidence Appendix"])
    if run_export.evidence is None:
        sections.extend(["", "_No sealed EvidenceBundle was recorded for this run._"])
    else:
        table_refs = {
            ref
            for table in run_export.evidence.tables
            for ref in table.evidence_refs
        }
        sections.extend(
            [
                "",
                f"- Bundle version: `{run_export.evidence.version}`",
                f"- Digest: `{run_export.evidence.digest}`",
                f"- Analysis date: `{run_export.evidence.analysis_date}`",
            ]
        )
        if run_export.evidence.tables:
            sections.extend(["", "### Raw Evidence Tables"])
            for table in run_export.evidence.tables:
                sections.extend(
                    [
                        "",
                        f"#### {table.title}",
                        "",
                        f"- Table: `{table.id}`",
                        f"- Purpose: {table.purpose}",
                        f"- Rows: `{len(table.rows)}`",
                        f"- Raw data: `tables/{table.id}.csv` in the research package",
                        "- Evidence: " + _render_refs(table.evidence_refs),
                    ]
                )
            sections.extend(["", "### Evidence Items"])
        for group in group_evidence_by_content(run_export.evidence.items):
            item = group.canonical
            sources = tuple(
                dict.fromkeys(
                    origin.source for grouped_item in group.items for origin in grouped_item.origins
                )
            ) or tuple(dict.fromkeys(grouped_item.source for grouped_item in group.items))
            sections.extend(
                [
                    "",
                    f"### `{item.ref}`",
                    "",
                    "- Refs: " + ", ".join(f"`{ref}`" for ref in group.refs),
                    f"- Sources: {', '.join(sources)}",
                    f"- Type: {item.evidence_type}",
                    f"- Quality: `{item.quality.value}`",
                    f"- Fallback: `{str(item.fallback).lower()}`",
                ]
            )
            if group.content and table_refs.isdisjoint(group.refs):
                sections.extend(
                    [
                        "",
                        "#### Content",
                        "",
                        group.content,
                    ]
                )
            elif group.content:
                sections.extend(
                    [
                        "",
                        "_Raw tabular content is available in `evidence.json` "
                        "and the linked `tables/*.csv` files._",
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


def render_run_export_package(run_export: RunExport) -> bytes:
    """Build a self-verifying ZIP with a readable report and raw audit data."""

    payloads: dict[str, bytes] = {
        "report.md": render_run_export_markdown(run_export).encode(),
        "run.json": _json_bytes(
            {
                "schema_version": run_export.schema_version,
                "run": run_export.run.model_dump(mode="json"),
                "result": run_export.result.model_dump(mode="json"),
                "attempts": [
                    attempt.model_dump(mode="json")
                    for attempt in run_export.attempts
                ],
            }
        ),
        "artifacts.json": _json_bytes(
            [
                artifact.model_dump(mode="json")
                for artifact in run_export.artifacts
            ]
        ),
        "evidence.json": _json_bytes(
            run_export.evidence.model_dump(mode="json")
            if run_export.evidence is not None
            else None
        ),
    }
    if run_export.evidence is not None:
        for table in run_export.evidence.tables:
            payloads[f"tables/{table.id}.csv"] = _evidence_table_csv(table)

    manifest = {
        "schema_version": "1",
        "run_id": run_export.run.id,
        "files": [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in sorted(payloads.items())
        ],
    }
    payloads["manifest.json"] = _json_bytes(manifest)

    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path, content in sorted(payloads.items()):
            archive.writestr(path, content)
    return output.getvalue()


def _evidence_table_csv(table: EvidenceTable) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["row_id", *(column.key for column in table.columns)])
    for row in table.rows:
        writer.writerow(
            [
                row.id,
                *(
                    _csv_raw_value(row.cells[column.key].raw_value)
                    for column in table.columns
                ),
            ]
        )
    return output.getvalue().encode()


def _csv_raw_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _escape_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _render_research_table(table: ResearchTable) -> list[str]:
    headers = "| " + " | ".join(_escape_table_cell(column.label) for column in table.columns) + " |"
    divider = "|" + "|".join("---" for _column in table.columns) + "|"
    rows = [
        "| "
        + " | ".join(
            _escape_table_cell(row.cells[column.key].display_value) for column in table.columns
        )
        + " |"
        for row in table.rows
    ]
    source_note = (
        (
            f"- Source view: `{len(table.rows)}/{table.total_source_rows}` "
            f"rows from `{table.source_evidence_table_id}`"
        )
        if table.source_evidence_table_id is not None
        else "- Source view: synthesized comparison"
    )
    lines = [
        f"##### {table.title}",
        "",
        "**AI research table**",
        "",
        f"- Table: `{table.id}`",
        f"- Purpose: {table.purpose}",
        "- Evidence: " + _render_refs(table.evidence_refs),
        source_note,
        "",
        headers,
        divider,
        *rows,
    ]
    lines.extend(_render_table_cell_audit(table.rows, table.columns))
    return lines


def _render_table_cell_audit(
    rows: tuple[ResearchTableRow, ...],
    columns: tuple[ResearchTableColumn, ...],
) -> list[str]:
    """Render cell provenance and derivations without cluttering data values."""

    entries = []
    for row in rows:
        if row.evidence_refs:
            entries.append(
                f"- `{row.id}`: evidence "
                + ", ".join(f"`{ref}`" for ref in row.evidence_refs)
            )
        for column in columns:
            cell = row.cells[column.key]
            details = []
            if cell.evidence_refs:
                details.append("evidence " + ", ".join(f"`{ref}`" for ref in cell.evidence_refs))
            if cell.derived is not None:
                inputs = ", ".join(f"{name}={value}" for name, value in cell.derived.inputs.items())
                details.extend(
                    [
                        f"formula `{cell.derived.formula}`",
                        f"inputs `{inputs}`",
                        f"result `{cell.derived.result}`",
                    ]
                )
            if details:
                entries.append(f"- `{row.id}.{column.key}`: " + "; ".join(details))
    if not entries:
        return []
    return ["", "**Cell audit**", "", *entries]


def _render_analyst_report(
    report: AnalystReport,
) -> str:
    research_tables = {table.id: table for table in report.tables}
    lines = [
        "#### Executive Summary",
        "",
        f"- Analyst: `{report.analyst}`",
        f"- Confidence: `{report.confidence:.0%}`",
        "- Evidence: " + _render_refs(report.evidence_refs),
        "",
        report.executive_summary,
    ]
    for section in report.sections:
        lines.extend(["", f"#### {section.title}", "", section.narrative])
        if section.evidence_table_ids:
            lines.extend(
                [
                    "",
                    "- Source data: "
                    + ", ".join(
                        f"`{table_id}`"
                        for table_id in section.evidence_table_ids
                    ),
                ]
            )
        for table_id in section.research_table_ids:
            if table_id in research_tables:
                lines.extend(["", *_render_research_table(research_tables[table_id])])
    lines.extend(["", "#### Auditable Claims"])
    for claim in report.claims:
        lines.extend(
            [
                "",
                f"##### `{claim.id}` · {claim.kind.value}",
                "",
                f"- Confidence: `{claim.confidence:.0%}`",
                "- Evidence: " + _render_refs(claim.evidence_refs),
                "",
                claim.statement,
                "",
                f"**Implication:** {claim.implication}",
            ]
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
    content: ResearchArtifactContent,
) -> str:
    if isinstance(content, AnalystReport):
        return _render_analyst_report(content)
    if isinstance(content, ResearchCase):
        return _render_research_case(content)
    if isinstance(content, DebateAgenda):
        return _render_debate_agenda(content)
    if isinstance(content, RebuttalReview):
        return _render_rebuttal_review(content)
    if isinstance(content, JudgeDraft):
        return _render_judge_draft(content)
    if isinstance(content, RiskReview):
        return _render_risk_review(content)
    if isinstance(content, ResearchDecision):
        return _render_research_decision(content)
    raise TypeError(f"unsupported research artifact: {type(content)!r}")


def _render_research_case(content: ResearchCase) -> str:
    lines = [
        f"#### {content.role.title()} Case",
        "",
        "##### Executive Summary",
        "",
        content.executive_summary,
        "",
        "##### Thesis",
        "",
        content.thesis,
        "",
        "##### Arguments",
    ]
    for argument in content.arguments:
        lines.extend(
            [
                "",
                f"###### `{argument.id}`",
                "",
                f"- Claims: {_render_ids(argument.claim_ids)}",
                f"- Confidence: `{argument.confidence:.0%}`",
                f"- Evidence: {_render_refs(argument.evidence_refs)}",
                "",
                argument.statement,
                "",
                f"**Mechanism:** {argument.mechanism}",
                "",
                f"**Implication:** {argument.implication}",
            ]
        )
    lines.extend(
        _render_list(
            "Strongest Counterarguments",
            content.strongest_counterarguments,
        )
    )
    lines.extend(_render_list("Fragile Assumptions", content.fragile_assumptions))
    lines.extend(_render_list("Catalysts", content.catalysts))
    lines.extend(_render_list("Risks", content.risks))
    lines.extend(["", "##### Evidence", "", _render_refs(content.evidence_refs)])
    return "\n".join(lines)


def _render_debate_agenda(content: DebateAgenda) -> str:
    lines = [
        "#### Debate Agenda",
        "",
        content.executive_summary,
        "",
        "##### Material Issues",
    ]
    for issue in content.issues:
        lines.extend(
            [
                "",
                f"###### `{issue.id}` · {issue.importance.value}",
                "",
                issue.question,
                "",
                f"- Claims: {_render_ids(issue.claim_ids)}",
                f"- Evidence: {_render_refs(issue.evidence_refs)}",
                "",
                f"**Bull position:** {issue.bull_position}",
                "",
                f"**Bear position:** {issue.bear_position}",
            ]
        )
    return "\n".join(lines)


def _render_rebuttal_review(content: RebuttalReview) -> str:
    lines = [
        f"#### {content.role.title()} Rebuttal · Round {content.round}",
        "",
        "##### Thesis Update",
        "",
        content.thesis_update,
        "",
        "##### Issue-by-Issue Responses",
    ]
    for response in content.responses:
        lines.extend(
            [
                "",
                f"###### `{response.agenda_id}` · {response.outcome.value}",
                "",
                f"- Claims: {_render_ids(response.claim_ids)}",
                f"- Evidence: {_render_refs(response.evidence_refs)}",
                ("- New evidence: " + _render_refs(response.new_evidence_refs)),
                "",
                response.response,
                "",
                f"**Causal mechanism:** {response.causal_mechanism}",
            ]
        )
        lines.extend(
            _render_list(
                "Remaining Questions",
                response.remaining_questions,
                level=6,
            )
        )
    lines.extend(_render_list("Review-level Remaining Questions", content.remaining_questions))
    lines.extend(
        [
            "",
            "##### New Evidence",
            "",
            _render_refs(content.new_evidence_refs),
        ]
    )
    return "\n".join(lines)


def _render_judge_draft(content: JudgeDraft) -> str:
    lines = [
        "#### Research Judge Draft",
        "",
        f"- Preliminary rating: **{content.preliminary_rating.value}**",
        f"- Confidence: `{content.confidence:.0%}`",
        f"- Time horizon: {content.time_horizon}",
        f"- Evidence: {_render_refs(content.evidence_refs)}",
        f"- Memory: {_render_ids(content.memory_refs)}",
        "",
        "##### Executive Summary",
        "",
        content.executive_summary,
        "",
        "##### Thesis",
        "",
        content.thesis,
        "",
        "##### Dispute Rulings",
    ]
    for ruling in content.rulings:
        lines.extend(
            [
                "",
                f"###### `{ruling.agenda_id}` · {ruling.resolution.value}",
                "",
                f"- Accepted claims: {_render_ids(ruling.accepted_claim_ids)}",
                f"- Rejected claims: {_render_ids(ruling.rejected_claim_ids)}",
                f"- Evidence: {_render_refs(ruling.evidence_refs)}",
                "",
                ruling.rationale,
            ]
        )
    lines.extend(_render_list("Catalysts", content.catalysts))
    lines.extend(_render_list("Risks", content.risks))
    lines.extend(
        _render_list(
            "Invalidation Conditions",
            content.invalidation_conditions,
        )
    )
    lines.extend(_render_list("Unresolved Questions", content.unresolved_questions))
    return "\n".join(lines)


def _render_risk_review(content: RiskReview) -> str:
    lines = [
        f"#### {content.role.title()} Risk Review",
        "",
        f"- Confidence adjustment: `{content.confidence_adjustment:+.0%}`",
        f"- Evidence: {_render_refs(content.evidence_refs)}",
        "",
        content.executive_summary,
        "",
        "##### Findings",
    ]
    for finding in content.findings:
        lines.extend(
            [
                "",
                (f"###### `{finding.id}` · {finding.kind.value} · {finding.severity.value}"),
                "",
                f"- Related claims: {_render_ids(finding.related_claim_ids)}",
                f"- Evidence: {_render_refs(finding.evidence_refs)}",
                "",
                finding.statement,
                "",
                f"**Mechanism:** {finding.mechanism}",
            ]
        )
    lines.extend(_render_list("Invalidation Paths", content.invalidation_paths))
    lines.extend(_render_list("Recommended Changes", content.recommended_changes))
    return "\n".join(lines)


def _render_research_decision(content: ResearchDecision) -> str:
    lines = [
        "> Non-personalized research opinion. This is not an account-level "
        "instruction, position size, or order.",
        "",
        f"- Rating: **{content.rating.value}**",
        f"- Confidence: `{content.confidence:.0%}`",
        f"- Time horizon: {content.time_horizon}",
        f"- Evidence: {_render_refs(content.evidence_refs)}",
        f"- Memory: {_render_ids(content.memory_refs)}",
        "",
        "### Executive Summary",
        "",
        content.executive_summary,
        "",
        "### Thesis",
        "",
        content.thesis,
        "",
        "### Scenarios",
    ]
    for scenario in content.scenarios:
        lines.extend(
            [
                "",
                f"#### {scenario.kind.value.title()}",
                "",
                scenario.outcome,
            ]
        )
        lines.extend(
            _render_list(
                "Core Assumptions",
                scenario.core_assumptions,
                level=5,
            )
        )
        if scenario.valuation_range is not None:
            lines.extend(
                [
                    "",
                    (
                        "**Valuation range:** "
                        f"`{scenario.valuation_range.low}`–"
                        f"`{scenario.valuation_range.high}`"
                    ),
                ]
            )
        lines.extend(
            [
                "",
                f"**Evidence:** {_render_refs(scenario.evidence_refs)}",
            ]
        )
    if content.valuation_assessment is not None:
        assessment = content.valuation_assessment
        lines.extend(
            [
                "",
                "### Valuation Assessment",
                "",
                f"- Method: {assessment.method}",
                (
                    "- Range: "
                    f"`{assessment.valuation_range.low}`–"
                    f"`{assessment.valuation_range.high}` "
                    f"{assessment.currency}"
                ),
                f"- As of: `{assessment.as_of_date.isoformat()}`",
                ("- Input evidence: " + _render_refs(assessment.input_evidence_refs)),
            ]
        )
        lines.extend(_render_list("Limitations", assessment.limitations))
    lines.extend(["", "### Market Reference Levels"])
    if content.market_reference_levels:
        for level in content.market_reference_levels:
            lines.extend(
                [
                    "",
                    f"#### {level.level_type.replace('_', ' ').title()}",
                    "",
                    f"- Value: `{level.value}` {level.unit}",
                    f"- As of: `{level.as_of_date.isoformat()}`",
                    f"- Evidence: {_render_refs(level.evidence_refs)}",
                    "",
                    level.interpretation,
                ]
            )
    else:
        lines.extend(["", "_None identified._"])
    lines.extend(_render_list("Catalysts", content.catalysts))
    lines.extend(_render_list("Risks", content.risks))
    lines.extend(
        _render_list(
            "Invalidation Conditions",
            content.invalidation_conditions,
        )
    )
    lines.extend(_render_list("Unresolved Questions", content.unresolved_questions))
    lines.extend(["", "### Final Committee Response to Risk Review"])
    if content.risk_review_adjustments:
        for adjustment in content.risk_review_adjustments:
            lines.extend(
                [
                    "",
                    (f"#### {adjustment.source_role.title()} · {adjustment.disposition.value}"),
                    "",
                    f"**{adjustment.subject}**",
                    "",
                    adjustment.explanation,
                    "",
                    f"**Evidence:** {_render_refs(adjustment.evidence_refs)}",
                ]
            )
    else:
        lines.extend(["", "_No risk-review adjustments were recorded._"])
    return "\n".join(lines)


def _render_list(
    title: str,
    items: tuple[str, ...],
    *,
    level: int = 5,
) -> list[str]:
    prefix = "#" * level
    return [
        "",
        f"{prefix} {title}",
        "",
        *([f"- {item}" for item in items] if items else ["- None identified."]),
    ]


def _render_refs(refs: tuple[str, ...]) -> str:
    return ", ".join(f"`{ref}`" for ref in refs) or "none"


def _render_ids(refs: tuple[str, ...]) -> str:
    return ", ".join(f"`{ref}`" for ref in refs) or "none"


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
