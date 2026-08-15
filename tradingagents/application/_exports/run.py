"""Render self-contained, explicit run exports from durable contracts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping
from typing import Any

from tradingagents.application.markdown_evidence import normalize_evidence_markdown
from tradingagents.application.numeric_display import format_decision_number

from ..contracts import (
    AnalystReport,
    DebateAgenda,
    DecisionBrief,
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
from ..evidence import group_evidence_by_content
from .labels import ExportLabels, export_labels
from .serialization import json_bytes, zip_bytes


def render_run_export_markdown(run_export: RunExport) -> str:
    """Render a human-readable audit document without hidden model messages."""
    result = run_export.result
    labels = export_labels(run_export)
    evidence_aliases = _evidence_aliases(run_export.evidence)
    process_artifacts = tuple(
        artifact
        for artifact in run_export.artifacts
        if artifact.stage not in {"analyst", "decision"}
    )
    sections = [
        f"# {labels['title']}: {result.instrument}",
        "",
        f"- {labels['export_schema']}: `{run_export.schema_version}`",
        f"- {labels['run']}: `{result.run_id}`",
        f"- {labels['status']}: `{result.status.value}`",
        f"- {labels['attempt']}: `{run_export.run.attempt}`",
        "",
        f"## {labels['reports']}",
    ]
    if not result.reports:
        sections.extend(["", f"_{labels['no_reports']}_"])
    for name, report in result.reports.items():
        narrative = (
            _render_export_markdown(_render_analyst_report(report, labels), evidence_aliases)
            if isinstance(report, AnalystReport)
            else _render_export_markdown(str(report), evidence_aliases)
        )
        sections.extend(
            [
                "",
                f"### {labels.report_name(name)}",
                "",
                narrative,
            ]
        )

    sections.extend(["", f"## {labels['research_process']}"])
    if not process_artifacts:
        sections.extend(
            [
                "",
                f"_{labels['no_process']}_",
            ]
        )
    for artifact in process_artifacts:
        artifact_title = (
            labels["decision_brief"]
            if isinstance(artifact.content, DecisionBrief)
            else artifact.stage
        )
        sections.extend(
            [
                "",
                (f"### {artifact_title} · {artifact.role} · {labels['round']} {artifact.round}"),
                "",
                f"- {labels['artifact']}: `{artifact.id}`",
                f"- {labels['attempt']}: `{artifact.attempt}`",
                f"- {labels['schema']}: `{artifact.schema_version}`",
                f"- {labels['prompt']}: `{artifact.prompt_version}`",
                f"- {labels['generation']}: `{artifact.generation_method.value}`",
                f"- {labels['created']}: `{artifact.created_at.isoformat()}`",
            ]
        )
        sections.extend(
            "- "
            f"{labels['generation_observation']}: "
            f"`{observation.node}` · `{observation.task_kind}` · "
            f"`{observation.client_role}` · "
            f"`{observation.generation_method.value}`"
            for observation in artifact.generation_observations
        )
        human_text = _render_export_markdown(
            _artifact_human_text(artifact.content, labels),
            evidence_aliases,
        )
        if isinstance(artifact.content, DecisionBrief):
            sections.extend(["", f"> **{labels['decision_brief_notice']}**"])
        if human_text:
            sections.extend(["", human_text])

    sections.extend(["", f"## {labels['research_decision']}"])
    if result.decision is None:
        sections.extend(["", f"_{labels['no_decision']}_"])
    else:
        sections.extend(
            [
                "",
                _render_export_markdown(
                    _render_research_decision(result.decision, labels),
                    evidence_aliases,
                ),
            ]
        )

    if result.numeric_audit is not None:
        sections.extend(
            [
                "",
                _render_numeric_audit_appendix(
                    result.numeric_audit,
                    labels,
                    evidence_aliases,
                ),
            ]
        )

    warnings = _export_warnings(run_export)
    sections.extend(["", f"## {labels['structured_recoveries']}"])
    if not result.recoveries:
        sections.extend(["", f"_{labels['no_recoveries']}_"])
    else:
        for recovery in result.recoveries:
            issues = ", ".join(recovery.validation_issue_codes) or "—"
            sections.extend(
                [
                    "",
                    f"### `{recovery.node}`",
                    "",
                    f"- {labels['attempt']}: `{recovery.attempt}`",
                    f"- {labels['initial_reason']}: `{recovery.initial_reason_code}`",
                    f"- {labels['recovery_method']}: `{recovery.recovery_method.value}`",
                    f"- {labels['validation_issues']}: `{issues}`",
                    f"- {labels['retry_count']}: `{recovery.retry_count}`",
                    f"- {labels['recovered_at']}: `{recovery.recovered_at.isoformat()}`",
                ]
            )
    sections.extend(["", f"## {labels['warnings']}"])
    if not warnings:
        sections.extend(["", f"_{labels['no_warnings']}_"])
    else:
        for warning in warnings:
            details = []
            if warning.source:
                details.append(f"{labels['source']}: {warning.source}")
            if warning.evidence_ref:
                details.append(
                    f"{labels['evidence']}: "
                    + _render_alias_refs((warning.evidence_ref,), evidence_aliases, labels)
                )
            suffix = f" ({'; '.join(details)})" if details else ""
            message = labels.values.get(f"warning.{warning.code}", warning.message)
            sections.append(f"- **{warning.code}**: {message}{suffix}")

    metrics = result.metrics
    sections.extend(
        [
            "",
            f"## {labels['performance']}",
            "",
            f"_{labels['usage_note']}_",
            "",
            f"- {labels['llm_calls']}: `{metrics.llm_calls}`",
            f"- {labels['tool_calls']}: `{metrics.tool_calls}`",
            f"- {labels['input_tokens']}: `{metrics.input_tokens}`",
            f"- {labels['output_tokens']}: `{metrics.output_tokens}`",
            f"- {labels['cache_hit']}: `{metrics.cache_hit_input_tokens}`",
            f"- {labels['cache_miss']}: `{metrics.cache_miss_input_tokens}`",
            f"- {labels['reasoning']}: `{metrics.reasoning_output_tokens}`",
            (f"- {labels['detailed_calls']}: `{metrics.detailed_usage_calls}/{metrics.llm_calls}`"),
            f"- {labels['wall_time']}: `{metrics.wall_time_seconds:.3f}s`",
        ]
    )
    node_names = set(metrics.node_metrics)
    if node_names:
        sections.extend(
            [
                "",
                f"| {labels['node']} | {labels['llm_calls']} | {labels['tool_calls']} | "
                f"{labels['input_tokens']} | {labels['cache_hit']} | "
                f"{labels['cache_miss']} | {labels['output_tokens']} | "
                f"{labels['reasoning']} | {labels['detailed_calls']} | "
                f"{labels['wall_time']} |",
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

    sections.extend(["", f"### {labels['attempts']}"])
    if not run_export.attempts:
        sections.extend(["", f"_{labels['no_attempts']}_"])
    else:
        sections.extend(
            [
                "",
                f"| {labels['attempt']} | {labels['status_label']} | "
                f"{labels['resumes']} | {labels['error']} | {labels['llm_calls']} | "
                f"{labels['tool_calls']} | {labels['input_tokens']} | "
                f"{labels['output_tokens']} | {labels['wall_time']} |",
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

    sections.extend(["", f"## {labels['sources']}"])
    if run_export.evidence is None:
        sections.extend(["", f"_{labels['no_evidence']}_"])
    else:
        table_refs = {ref for table in run_export.evidence.tables for ref in table.evidence_refs}
        sections.extend(
            [
                "",
                f"- {labels['bundle_version']}: `{run_export.evidence.version}`",
                f"- {labels['digest']}: `{run_export.evidence.digest}`",
                f"- {labels['analysis_date']}: `{run_export.evidence.analysis_date}`",
            ]
        )
        if run_export.evidence.tables:
            sections.extend(["", f"### {labels['raw_tables']}"])
            for table in run_export.evidence.tables:
                sections.extend(
                    [
                        "",
                        f"#### {table.title}",
                        "",
                        f"- {labels['table']}: `{table.id}`",
                        f"- {labels['purpose']}: {table.purpose}",
                        f"- {labels['rows']}: `{len(table.rows)}`",
                        f"- {labels['raw_data']}: `tables/{table.id}.csv`",
                        f"- {labels['evidence']}: "
                        + _render_canonical_refs(table.evidence_refs, labels),
                    ]
                )
            sections.extend(["", f"### {labels['evidence_items']}"])
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
                    f"- {labels['refs']}: " + ", ".join(f"`{ref}`" for ref in group.refs),
                    f"- {labels['source_list']}: {', '.join(sources)}",
                    f"- {labels['type']}: {item.evidence_type}",
                    f"- {labels['quality']}: `{item.quality.value}`",
                    f"- {labels['fallback']}: `{str(item.fallback).lower()}`",
                ]
            )
            if group.content and table_refs.isdisjoint(group.refs):
                sections.extend(
                    [
                        "",
                        f"#### {labels['content']}",
                        "",
                        group.content,
                    ]
                )
            elif group.content:
                sections.extend(
                    [
                        "",
                        f"_{labels['raw_table_available']}_",
                    ]
                )
            sections.extend(
                [
                    "",
                    f"#### {labels['audit_records']}",
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
        "run.json": json_bytes(
            {
                "schema_version": run_export.schema_version,
                "run": run_export.run.model_dump(mode="json"),
                "result": run_export.result.model_dump(mode="json"),
                "attempts": [attempt.model_dump(mode="json") for attempt in run_export.attempts],
            }
        ),
        "artifacts.json": json_bytes(
            [artifact.model_dump(mode="json") for artifact in run_export.artifacts]
        ),
        "evidence.json": json_bytes(
            run_export.evidence.model_dump(mode="json") if run_export.evidence is not None else None
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
    payloads["manifest.json"] = json_bytes(manifest)
    return zip_bytes(dict(sorted(payloads.items())), compresslevel=9)


def _evidence_table_csv(table: EvidenceTable) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["row_id", *(column.key for column in table.columns)])
    for row in table.rows:
        writer.writerow(
            [
                row.id,
                *(_csv_raw_value(row.cells[column.key].raw_value) for column in table.columns),
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


def _render_analyst_report(
    report: AnalystReport,
    labels: ExportLabels,
) -> str:
    confidence = (
        f"{report.confidence:.0%}" if report.confidence is not None else labels["not_audited"]
    )
    lines = [
        f"- {labels['analyst']}: `{report.analyst}`",
        f"- {labels['audit']}: `{report.audit_status.value}`",
        f"- {labels['confidence']}: `{confidence}`",
        "",
        report.markdown,
    ]
    if report.key_claims:
        lines.extend(["", f"#### {labels['key_claim_audit']}"])
        for claim in report.key_claims:
            lines.extend(
                [
                    "",
                    "- "
                    f"{labels.enum_name('claim_importance', claim.importance.value)}"
                    " · "
                    f"{labels.enum_name('claim_kind', claim.kind.value)}: "
                    f"{claim.statement}",
                    f"  - {labels['implication']}: {claim.implication}",
                    f"  - {labels['evidence']}: {_render_refs(claim.evidence_refs, labels)}",
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
    if not markdown:
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
    labels: ExportLabels,
) -> str:
    if isinstance(content, AnalystReport):
        return _render_analyst_report(content, labels)
    if isinstance(content, DecisionBrief):
        return content.markdown
    if isinstance(content, ResearchCase):
        return _render_research_case(content)
    if isinstance(content, DebateAgenda):
        return _render_debate_agenda(content, labels)
    if isinstance(content, RebuttalReview):
        return _render_rebuttal_review(content)
    if isinstance(content, JudgeDraft):
        return _render_judge_draft(content)
    if isinstance(content, RiskReview):
        return _render_risk_review(content)
    if isinstance(content, ResearchDecision):
        return _render_research_decision(content, labels)
    raise TypeError(f"unsupported research artifact: {type(content)!r}")


def _render_research_case(content: ResearchCase) -> str:
    return content.markdown


def _render_debate_agenda(content: DebateAgenda, labels: ExportLabels) -> str:
    lines = [
        f"#### {labels['debate_agenda']}",
        "",
        content.summary,
        "",
        f"##### {labels['material_issues']}",
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


def _render_research_decision(
    content: ResearchDecision,
    labels: ExportLabels,
) -> str:
    calculation_uses = _calculation_uses(content, labels)
    lines = [
        f"> {labels['opinion_notice']}",
        "",
        f"- {labels['rating']}: **{content.rating.value}**",
        f"- {labels['confidence']}: `{content.confidence:.0%}`",
        f"- {labels['time_horizon']}: {content.time_horizon}",
        (
            f"- {labels['numeric_audit']}: `"
            + (
                labels.enum_name("numeric_status", content.numeric_audit_status.value)
                if content.numeric_audit_status is not None
                else labels["not_recorded"]
            )
            + "`"
        ),
        f"- {labels['evidence']}: {_render_refs(content.evidence_refs, labels)}",
        f"- {labels['memory']}: {_render_ids(content.memory_refs, labels)}",
        "",
        f"### {labels['executive_summary']}",
        "",
        content.executive_summary,
        "",
        f"### {labels['thesis']}",
        "",
        content.thesis,
        "",
        f"### {labels['scenarios']}",
    ]
    for scenario in content.scenarios:
        lines.extend(
            [
                "",
                f"#### {labels[scenario.kind.value]}",
                "",
                scenario.outcome,
            ]
        )
        lines.extend(
            _render_list(
                labels["core_assumptions"],
                scenario.core_assumptions,
                level=5,
                labels=labels,
            )
        )
        for reference_range in scenario.reference_ranges:
            display_unit = f" {reference_range.unit}" if reference_range.unit else ""
            lines.extend(
                [
                    "",
                    (
                        f"**{labels['scenario_reference_range']} "
                        f"({labels.enum_name('category', reference_range.category.value)} · "
                        f"{reference_range.label}):** "
                        f"`{format_decision_number(reference_range.low.value, reference_range.unit, output_language=labels.language)}`–"
                        f"`{format_decision_number(reference_range.high.value, reference_range.unit, output_language=labels.language)}`"
                        f"{display_unit}"
                    ),
                    (
                        f"**{labels['endpoint_basis']}:** "
                        f"{labels[f'basis.{reference_range.low.basis.value}']} / "
                        f"{labels[f'basis.{reference_range.high.basis.value}']}"
                    ),
                    (
                        f"**{labels['endpoint_dates']}:** "
                        f"`{reference_range.low.as_of_date.isoformat()}` "
                        f"({labels.enum_name('temporal', reference_range.low.temporal_basis.value)}) / "
                        f"`{reference_range.high.as_of_date.isoformat()}` "
                        f"({labels.enum_name('temporal', reference_range.high.temporal_basis.value)})"
                    ),
                    reference_range.interpretation,
                ]
            )
            lines.extend(
                _render_list(
                    labels["limitations"],
                    reference_range.limitations,
                    labels=labels,
                )
            )
        lines.extend(
            [
                "",
                f"**{labels['evidence']}:** {_render_refs(scenario.evidence_refs, labels)}",
            ]
        )
    if content.valuation_assessment is not None:
        assessment = content.valuation_assessment
        lines.extend(
            [
                "",
                f"### {labels['valuation_assessment']}",
                "",
                f"- {labels['method']}: {assessment.method}",
                (
                    f"- {labels['range']}: "
                f"`{format_decision_number(assessment.low.value, assessment.unit, output_language=labels.language)}`–"
                f"`{format_decision_number(assessment.high.value, assessment.unit, output_language=labels.language)}` "
                f"{assessment.unit}"
                ),
                f"- {labels['as_of']}: `{assessment.as_of_date.isoformat()}`",
                (
                    f"- {labels['temporal_basis']}: "
                    f"{labels.enum_name('temporal', assessment.low.temporal_basis.value)} / "
                    f"{labels.enum_name('temporal', assessment.high.temporal_basis.value)}"
                ),
                f"- {labels['input_evidence']}: "
                + _render_refs(assessment.input_evidence_refs, labels),
                f"- {labels['calculations']}: " + _render_ids(assessment.calculation_ids, labels),
            ]
        )
        lines.extend(_render_list(labels["limitations"], assessment.limitations, labels=labels))
    lines.extend(["", f"### {labels['market_references']}"])
    if content.market_reference_levels:
        for level in content.market_reference_levels:
            display_unit = f" {level.unit}" if level.unit else ""
            lines.extend(
                [
                    "",
                    f"#### {level.label}",
                    "",
                    f"- {labels['value']}: "
                    f"`{format_decision_number(level.value, level.unit, output_language=labels.language)}`"
                    f"{display_unit}",
                    f"- {labels['as_of']}: `{level.as_of_date.isoformat()}`",
                    f"- {labels['evidence']}: {_render_refs(level.evidence_refs, labels)}",
                    f"- {labels['basis']}: {labels[f'basis.{level.basis.value}']}",
                    f"- {labels['temporal_basis']}: "
                    f"{labels.enum_name('temporal', level.temporal_basis.value)}",
                    (f"- {labels['calculations']}: " + _render_ids(level.calculation_ids, labels)),
                    "",
                    level.interpretation,
                ]
            )
    else:
        lines.extend(["", f"_{labels['no_market_references']}_"])
    lines.extend(["", f"### {labels['calculations']}"])
    if content.calculation_records:
        for calculation in content.calculation_records:
            inputs = ", ".join(
                f"{name}={format_decision_number(value, output_language=labels.language)}"
                for name, value in calculation.inputs.items()
            )
            lines.extend(
                [
                    "",
                    f"#### `{calculation.id}`",
                    "",
                    f"- {labels['used_by']}: "
                    + ", ".join(calculation_uses.get(calculation.id, ())),
                    f"- {labels['formula']}: `{calculation.formula}`",
                    f"- {labels['inputs']}: `{inputs}`",
                    f"- {labels['result']}: "
                    f"`{format_decision_number(calculation.result, calculation.unit, output_language=labels.language)}` "
                    f"{calculation.unit}",
                    f"- {labels['as_of']}: `{calculation.as_of_date.isoformat()}`",
                    f"- {labels['temporal_basis']}: "
                    f"{labels.enum_name('temporal', calculation.temporal_basis.value)}",
                    f"- {labels['evidence']}: "
                    + _render_refs(calculation.input_evidence_refs, labels),
                ]
            )
            lines.extend(
                _render_list(labels["limitations"], calculation.limitations, labels=labels)
            )
    else:
        lines.extend(["", f"_{labels['no_calculations']}_"])
    lines.extend(_render_list(labels["catalysts"], content.catalysts, labels=labels))
    lines.extend(_render_list(labels["risks"], content.risks, labels=labels))
    lines.extend(
        _render_list(
            labels["invalidation"],
            content.invalidation_conditions,
            labels=labels,
        )
    )
    lines.extend(_render_list(labels["unresolved"], content.unresolved_questions, labels=labels))
    lines.extend(["", f"### {labels['risk_response']}"])
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
                    f"**{labels['evidence']}:** {_render_refs(adjustment.evidence_refs, labels)}",
                ]
            )
    else:
        lines.extend(["", f"_{labels['no_adjustments']}_"])
    return "\n".join(lines)


def _calculation_uses(
    content: ResearchDecision,
    labels: ExportLabels,
) -> dict[str, tuple[str, ...]]:
    uses: dict[str, list[str]] = {}

    def add(calculation_ids: tuple[str, ...], label: str) -> None:
        for calculation_id in calculation_ids:
            uses.setdefault(calculation_id, []).append(label)

    for calculation in content.calculation_records:
        for use in calculation.decision_uses:
            add(
                (calculation.id,),
                labels["calculation_use.decision"].format(
                    location=_decision_component_label(use.component_path, labels),
                    label=use.label,
                ),
            )

    for scenario in content.scenarios:
        for reference_range in scenario.reference_ranges:
            add(
                tuple(
                    item
                    for item in (
                        reference_range.low.calculation_id,
                        reference_range.high.calculation_id,
                    )
                    if item is not None
                ),
                labels["calculation_use.scenario"].format(
                    scenario=labels[scenario.kind.value]
                ),
            )
    if content.valuation_assessment is not None:
        add(
            content.valuation_assessment.calculation_ids,
            labels["calculation_use.valuation"],
        )
    for level in content.market_reference_levels:
        add(
            level.calculation_ids,
            labels["calculation_use.market"].format(label=level.label),
        )
    return {calculation_id: tuple(dict.fromkeys(labels)) for calculation_id, labels in uses.items()}


def _decision_component_label(component_path: str, labels: ExportLabels) -> str:
    if component_path == "executive_summary":
        return labels["executive_summary"]
    if component_path == "thesis":
        return labels["thesis"]
    if component_path.startswith("risks."):
        return labels["risks"]
    if component_path.startswith("invalidation_conditions."):
        return labels["invalidation"]
    if component_path.startswith("risk_review_adjustments."):
        return labels["risk_response"]
    match = re.fullmatch(r"scenarios\.(base|bull|bear)\..+", component_path)
    if match:
        return labels["calculation_use.scenario"].format(
            scenario=labels[match.group(1)]
        )
    return labels["calculation_use.decision_claim"]


def _render_numeric_audit_appendix(
    appendix: DecisionNumericAuditAppendix,
    labels: ExportLabels,
    evidence_aliases: Mapping[str, str],
) -> str:
    has_snapshots = bool(appendix.snapshots)
    has_checks = bool(appendix.requirement_checks)
    lines = [
        f"## {labels['decision_requirement_audit'] if has_checks else labels['unverified_numeric'] if has_snapshots else labels['numeric_audit_gaps']}",
        "",
        f"- {labels['status_label']}: `{appendix.status.value}`",
    ]
    if has_snapshots or appendix.omitted_components:
        lines[1:1] = [
            "",
            f"> **{labels['warnings']}:** "
            f"{labels['numeric_warning'] if has_snapshots else labels['numeric_gap_warning']}",
        ]
    if appendix.requirement_checks:
        lines.extend(
            [
                "",
                f"### {labels['requirement_comparisons']}",
                "",
                (
                    f"| {labels['requirement_id']} | {labels['structured_value']} | "
                    f"{labels['canonical_result']} | {labels['comparison_precision']} | "
                    f"{labels['calculation_status']} | {labels['display_status']} |"
                ),
                "|---|---:|---:|---:|---|---|",
            ]
        )
        for check in appendix.requirement_checks:
            stated = format_decision_number(
                float(check.stated_value),
                check.unit,
                output_language=labels.language,
            )
            comparison = (
                format_decision_number(
                    float(check.comparison_result),
                    check.unit,
                    output_language=labels.language,
                )
                if check.comparison_result is not None
                else "—"
            )
            lines.append(
                f"| {check.label} (`{check.component_path}`) | {stated} {check.unit} | "
                f"{comparison}{f' {check.unit}' if check.comparison_result is not None else ''} | "
                f"{check.fraction_digits} | `{check.calculation_status.value}` | "
                f"`{check.display_status.value}` |"
            )
        for check in appendix.requirement_checks:
            refs = _render_alias_refs(
                check.input_evidence_refs,
                evidence_aliases,
                labels,
            )
            rounded = (
                f"{check.rounded_stated_value} / {check.rounded_canonical_result}"
                if check.rounded_stated_value is not None
                and check.rounded_canonical_result is not None
                else "—"
            )
            lines.extend(
                [
                    "",
                    f"- **{check.label}** (`{check.requirement_id}`)",
                    f"  - {labels['formula']}: `{check.formula}`",
                    f"  - {labels['inputs']}: `{json.dumps(check.inputs, ensure_ascii=False, sort_keys=True)}`",
                    f"  - {labels['rounded_comparison']}: `{rounded}`",
                    f"  - {labels['canonical_result']}: `{check.canonical_result}`",
                    f"  - {labels['display_scale']}: `{check.display_scale.value}`",
                    f"  - {labels['evidence']}: {refs or '—'}",
                ]
            )
            if check.issue_codes:
                lines.append(
                    f"  - {labels['issues']}: "
                    + ", ".join(f"`{code}`" for code in check.issue_codes)
                )
    elif not has_snapshots and not appendix.omitted_components:
        lines.extend(["", f"_{labels['comparison_not_recorded']}_"])
    if appendix.omitted_components:
        lines.extend(["", f"### {labels['omitted_components']}"])
        for item in appendix.omitted_components:
            lines.append(
                f"- **{_numeric_omission_label(item, labels)}** "
                f"(`{item.component_path}`): "
                + ", ".join(f"`{code}`" for code in item.issue_codes)
            )
    for snapshot in appendix.snapshots:
        phase_label = labels.enum_name("numeric_phase", snapshot.phase.value)
        lines.extend(
            [
                "",
                f"### {phase_label}",
                "",
                f"- {labels['method']}: `{snapshot.method.value}`",
                f"- {labels['reason']}: `{snapshot.reason_code}`",
                f"- {labels['schema_valid']}: `{str(snapshot.schema_valid).lower()}`",
                (
                    f"- {labels['issues']}: "
                    + (
                        ", ".join(f"`{code}`" for code in snapshot.validation_issues)
                        or f"_{labels['no_validation_issues']}_"
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
                f"- {labels['candidate_omitted']}: "
                f"`{snapshot.candidate_omitted}` "
                f"({labels['candidate_digest']} `{snapshot.candidate_digest}`)"
            )
        else:
            lines.append(f"- {labels['candidate_unparseable']}")
    return "\n".join(lines)


def _numeric_omission_label(item: Any, labels: ExportLabels) -> str:
    parts: list[str] = []
    if item.scenario_kind is not None:
        parts.append(labels[item.scenario_kind.value])
    parts.append(labels.enum_name("omission", item.component_type.value))
    if item.reference_label:
        parts.append(item.reference_label)
    return " · ".join(parts)


def _render_list(
    title: str,
    items: tuple[str, ...],
    *,
    level: int = 5,
    labels: ExportLabels,
) -> list[str]:
    prefix = "#" * level
    return [
        "",
        f"{prefix} {title}",
        "",
        *([f"- {item}" for item in items] if items else [f"- {labels['none']}"]),
    ]


def _render_refs(refs: tuple[str, ...], labels: ExportLabels) -> str:
    return " ".join(f"[^{ref}]" for ref in refs) or labels["none"]


def _render_ids(refs: tuple[str, ...], labels: ExportLabels) -> str:
    return ", ".join(f"`{ref}`" for ref in refs) or labels["none"]


def _render_canonical_refs(
    refs: tuple[str, ...],
    labels: ExportLabels,
) -> str:
    return ", ".join(f"`{ref}`" for ref in refs) or labels["none"]


def _render_alias_refs(
    refs: tuple[str, ...],
    aliases: Mapping[str, str],
    labels: ExportLabels,
) -> str:
    rendered = tuple(f"[{aliases[ref]}]" for ref in refs if ref in aliases)
    return " ".join(rendered) or labels["none"]


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
