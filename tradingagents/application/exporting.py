"""Render self-contained, explicit run exports from durable contracts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from typing import Any

from tradingagents.application.markdown_evidence import normalize_evidence_markdown

from .contracts import (
    AnalystReport,
    DebateAgenda,
    DecisionNumericAuditAppendix,
    EvidenceTable,
    JudgeDraft,
    RebuttalReview,
    ResearchArtifactContent,
    ResearchCase,
    ResearchDecision,
    ResearchWarning,
    RiskReview,
    RunExport,
)
from .evidence import group_evidence_by_content


def render_run_export_markdown(run_export: RunExport) -> str:
    """Render a human-readable audit document without hidden model messages."""
    result = run_export.result
    evidence_aliases = _evidence_aliases(run_export.evidence)
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
            _render_export_markdown(_render_analyst_report(report), evidence_aliases)
            if isinstance(report, AnalystReport)
            else _render_export_markdown(str(report), evidence_aliases)
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
        human_text = _render_export_markdown(
            _artifact_human_text(artifact.content),
            evidence_aliases,
        )
        if human_text:
            sections.extend(["", human_text])

    sections.extend(["", "## Research Decision"])
    if result.decision is None:
        sections.extend(["", "_No final decision was recorded._"])
    else:
        sections.extend(
            [
                "",
                _render_export_markdown(
                    _render_research_decision(result.decision),
                    evidence_aliases,
                ),
            ]
        )

    if result.numeric_audit is not None:
        sections.extend(
            [
                "",
                _render_numeric_audit_appendix(result.numeric_audit),
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

    sections.extend(["", "## Sources"])
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
            alias = evidence_aliases[item.ref]
            sources = tuple(
                dict.fromkeys(
                    origin.source for grouped_item in group.items for origin in grouped_item.origins
                )
            ) or tuple(dict.fromkeys(grouped_item.source for grouped_item in group.items))
            sections.extend(
                [
                    "",
                    f"### {alias}",
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


def _render_analyst_report(
    report: AnalystReport,
) -> str:
    confidence = (
        f"{report.confidence:.0%}"
        if report.confidence is not None
        else "not audited"
    )
    lines = [
        f"- Analyst: `{report.analyst}`",
        f"- Audit: `{report.audit_status.value}`",
        f"- Confidence: `{confidence}`",
        "",
        report.markdown,
    ]
    if report.key_claims:
        lines.extend(["", "#### Key Claim Audit"])
        for claim in report.key_claims:
            lines.extend(
                [
                    "",
                    f"- {claim.importance.value} · {claim.kind.value}: "
                    f"{claim.statement}",
                    f"  - Implication: {claim.implication}",
                    f"  - Evidence: {_render_refs(claim.evidence_refs)}",
                ]
            )
    return "\n".join(lines)


def _evidence_aliases(evidence: Any) -> dict[str, str]:
    if evidence is None:
        return {}
    aliases: dict[str, str] = {}
    for index, group in enumerate(group_evidence_by_content(evidence.items), 1):
        alias = f"E{index:02d}"
        for ref in group.refs:
            aliases[ref] = alias
    return aliases


def _render_export_markdown(
    markdown: str,
    aliases: dict[str, str],
) -> str:
    if not markdown or not aliases:
        return markdown
    normalized = normalize_evidence_markdown(
        markdown,
        allowed_refs=set(aliases),
        source="markdown export",
    )
    rendered = re.sub(
        r"\[\^(ev_[a-f0-9]{12})\]",
        lambda match: f"[{aliases[match.group(1)]}]",
        normalized.markdown,
    )
    return re.sub(r"(\[E\d+\])(?=\[E\d+\])", r"\1 ", rendered)


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
    return content.markdown


def _render_debate_agenda(content: DebateAgenda) -> str:
    lines = [
        "#### Debate Agenda",
        "",
        content.summary,
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
            ]
        )
    return "\n".join(lines)


def _render_rebuttal_review(content: RebuttalReview) -> str:
    return content.markdown


def _render_judge_draft(content: JudgeDraft) -> str:
    return content.markdown


def _render_risk_review(content: RiskReview) -> str:
    return content.markdown


def _render_research_decision(content: ResearchDecision) -> str:
    calculation_uses = _calculation_uses(content)
    lines = [
        "> Non-personalized research opinion. This is not an account-level "
        "instruction, position size, or order.",
        "",
        f"- Rating: **{content.rating.value}**",
        f"- Confidence: `{content.confidence:.0%}`",
        f"- Time horizon: {content.time_horizon}",
        (
            "- Numeric audit: `"
            + (
                content.numeric_audit_status.value
                if content.numeric_audit_status is not None
                else "not_recorded"
            )
            + "`"
        ),
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
            if scenario.valuation_calculation_ids:
                lines.append(
                    "**Calculation IDs:** "
                    + _render_ids(scenario.valuation_calculation_ids)
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
                ("- Calculations: " + _render_ids(assessment.calculation_ids)),
            ]
        )
        lines.extend(_render_list("Limitations", assessment.limitations))
    lines.extend(["", "### Market Reference Levels"])
    if content.market_reference_levels:
        for level in content.market_reference_levels:
            lines.extend(
                [
                    "",
                    f"#### {level.label}",
                    "",
                    f"- Value: `{level.value}` {level.unit}",
                    f"- As of: `{level.as_of_date.isoformat()}`",
                    f"- Evidence: {_render_refs(level.evidence_refs)}",
                    f"- Basis: `{level.basis.value}`",
                    (
                        "- Calculations: "
                        + _render_ids(level.calculation_ids)
                    ),
                    "",
                    level.interpretation,
                ]
            )
    else:
        lines.extend(["", "_None identified._"])
    lines.extend(["", "### Decision-Critical Calculations"])
    if content.calculation_records:
        for calculation in content.calculation_records:
            inputs = ", ".join(
                f"{name}={value}"
                for name, value in calculation.inputs.items()
            )
            lines.extend(
                [
                    "",
                    f"#### `{calculation.id}`",
                    "",
                    "- Used by: "
                    + ", ".join(calculation_uses.get(calculation.id, ())),
                    f"- Formula: `{calculation.formula}`",
                    f"- Inputs: `{inputs}`",
                    f"- Result: `{calculation.result}` {calculation.unit}",
                    f"- As of: `{calculation.as_of_date.isoformat()}`",
                    "- Evidence: "
                    + _render_refs(calculation.input_evidence_refs),
                ]
            )
            lines.extend(
                _render_list("Limitations", calculation.limitations)
            )
    else:
        lines.extend(["", "_No decision-critical calculations were recorded._"])
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


def _calculation_uses(content: ResearchDecision) -> dict[str, tuple[str, ...]]:
    uses: dict[str, list[str]] = {}

    def add(calculation_ids: tuple[str, ...], label: str) -> None:
        for calculation_id in calculation_ids:
            uses.setdefault(calculation_id, []).append(label)

    for scenario in content.scenarios:
        add(
            scenario.valuation_calculation_ids,
            f"{scenario.kind.value.title()} scenario",
        )
    if content.valuation_assessment is not None:
        add(content.valuation_assessment.calculation_ids, "Valuation assessment")
    for level in content.market_reference_levels:
        add(level.calculation_ids, f"Market reference: {level.label}")
    return {
        calculation_id: tuple(dict.fromkeys(labels))
        for calculation_id, labels in uses.items()
    }


def _render_numeric_audit_appendix(
    appendix: DecisionNumericAuditAppendix,
) -> str:
    lines = [
        "## Unverified Numeric Drafts",
        "",
        "> **Warning:** The following model-proposed numeric content did not "
        "pass audit and was not used in the canonical research decision.",
        "",
        f"- Status: `{appendix.status.value}`",
    ]
    if appendix.omitted_components:
        lines.extend(["", "### Omitted Components"])
        for item in appendix.omitted_components:
            lines.append(
                f"- **{item.label}** (`{item.component_path}`): "
                + ", ".join(f"`{code}`" for code in item.issue_codes)
            )
    for snapshot in appendix.snapshots:
        lines.extend(
            [
                "",
                f"### {snapshot.phase.value.title()} Candidate",
                "",
                f"- Method: `{snapshot.method.value}`",
                f"- Reason: `{snapshot.reason_code}`",
                f"- Schema valid: `{str(snapshot.schema_valid).lower()}`",
                (
                    "- Issues: "
                    + (
                        ", ".join(
                            f"`{code}`" for code in snapshot.validation_issues
                        )
                        or "_none recorded_"
                    )
                ),
            ]
        )
        if snapshot.candidate is not None:
            lines.extend(
                [
                    "",
                    "```json",
                    json.dumps(snapshot.candidate, ensure_ascii=False, indent=2),
                    "```",
                ]
            )
        elif snapshot.candidate_omitted:
            lines.append(
                f"- Candidate omitted: `{snapshot.candidate_omitted}` "
                f"(digest `{snapshot.candidate_digest}`)"
            )
        else:
            lines.append("- Candidate: _not parseable as a JSON object_")
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
