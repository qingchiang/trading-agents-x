"""Render self-contained, explicit run exports from durable contracts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping
from typing import Any

from tradingagents.application.export_labels import ExportLabels, export_labels
from tradingagents.domain.artifacts import ResearchArtifactContent
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.evidence import EvidenceBundle, EvidenceTable
from tradingagents.domain.evidence_tables import group_evidence_by_content
from tradingagents.domain.history import RunExport
from tradingagents.domain.numeric_display import format_decision_number
from tradingagents.domain.reports import (
    AnalystReport,
    DebateAgenda,
    DecisionBrief,
    JudgeDraft,
    RebuttalReview,
    ResearchCase,
    ResearchWarning,
    RiskReview,
)
from tradingagents.research.presentation import normalize_evidence_markdown


def _format_observation_value(value: float) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _performance_export_line(label: str, component: Any, labels: ExportLabels) -> str:
    calculation = component.calculation
    if calculation is None:
        reason = component.reason or labels["not_recorded"]
        return f"- **{label}:** {component.status.value} — {reason}"
    return (
        f"- **{label}:** {calculation.unrounded_return:+.2%}; "
        f"`{calculation.start_session}` · `{_format_observation_value(calculation.start_value)}` "
        f"→ `{calculation.end_session}` · "
        f"`{_format_observation_value(calculation.end_value)}`; "
        f"{labels['adjustment_basis']}: `{calculation.adjustment_basis}`"
    )


def render_run_export_markdown(run_export: RunExport) -> str:
    """Render a human-readable audit document without hidden model messages."""
    result = run_export.result
    labels = export_labels(run_export)
    baseline_evidence = (
        run_export.incremental_context.full_baseline_evidence
        if run_export.incremental_context is not None
        else None
    )
    evidence_aliases = _evidence_aliases(run_export.evidence, baseline_evidence)
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

    node = run_export.research_node
    if node is not None and node.research_kind == "incremental":
        sections.extend(["", f"## {labels['incremental_products']}", ""])
        brief = (
            run_export.incremental_context.analysis_brief
            if run_export.incremental_context is not None
            else None
        )
        sections.extend([f"### {labels['incremental_brief']}", ""])
        if brief is None:
            sections.append(f"_{labels['historical_brief_missing']}_")
        else:
            sections.append(_render_export_markdown(brief.markdown, evidence_aliases))
        outcome = node.decision_outcome
        outcome_key = (
            f"decision_outcome.{outcome.value}"
            if outcome is not None
            else "decision_outcome.not_recorded"
        )
        sections.extend(
            [
                "",
                f"### {labels['decision_outcome']}",
                "",
                f"- {labels[outcome_key]}",
            ]
        )
        if node.decision_outcome_reason:
            sections.append(f"- {node.decision_outcome_reason}")
        if node.performance is not None:
            sections.extend(
                [
                    "",
                    f"### {labels['performance']}",
                    "",
                    _performance_export_line(
                        labels["current_instrument"],
                        node.performance.stock,
                        labels,
                    ),
                ]
            )
            sections.extend(
                _performance_export_line(benchmark.name, benchmark.component, labels)
                for benchmark in node.performance.benchmarks
            )
        if node.information_advancement is not None:
            reasons = ", ".join(node.information_advancement.reasons) or "—"
            sections.append(f"- {labels['information_advancement']}: {reasons}")
        if node.research_availability is not None:
            availability = ", ".join(
                f"{item.domain}: {item.status.value}" for item in node.research_availability.domains
            )
            sections.append(f"- {labels['research_availability']}: {availability}")
        if node.reassessment is not None:
            sections.extend(["", f"### {labels['reassessment']}", ""])
            sections.extend(
                f"- `{entry.component_id}`: {entry.disposition.value} — {entry.reason}"
                for entry in node.reassessment.entries
            )
        if node.full_research_required_reasons:
            sections.extend(["", f"### {labels['full_research_required']}", ""])
            sections.extend(
                f"- `{reason.code}`: {reason.message}"
                for reason in node.full_research_required_reasons
            )
        context = run_export.incremental_context
        if context is not None:
            sections.extend(
                [
                    "",
                    f"### {labels['full_baseline_context']}",
                    "",
                    f"- {labels['run']}: `{context.full_baseline.run_id}`",
                    f"- {labels['analysis_date']}: `{context.full_baseline.analysis_date}`",
                    "",
                    _render_export_markdown(
                        _render_research_decision(context.full_baseline.decision, labels),
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

    _append_evidence_sources(
        sections,
        run_export.evidence,
        evidence_aliases,
        labels,
        title=labels["sources"],
        table_path_prefix="tables",
    )
    if baseline_evidence is not None:
        _append_evidence_sources(
            sections,
            baseline_evidence,
            evidence_aliases,
            labels,
            title=labels["baseline_sources"],
            table_path_prefix="baseline/tables",
        )
    return "\n".join(sections)


def _append_evidence_sources(
    sections: list[str],
    evidence: EvidenceBundle | None,
    evidence_aliases: Mapping[str, str],
    labels: ExportLabels,
    *,
    title: str,
    table_path_prefix: str,
) -> None:
    sections.extend(["", f"## {title}"])
    if evidence is None:
        sections.extend(["", f"_{labels['no_evidence']}_"])
        return

    table_refs = {ref for table in evidence.tables for ref in table.evidence_refs}
    sections.extend(
        [
            "",
            f"- {labels['bundle_version']}: `{evidence.version}`",
            f"- {labels['digest']}: `{evidence.digest}`",
            f"- {labels['analysis_date']}: `{evidence.analysis_date}`",
        ]
    )
    if evidence.tables:
        sections.extend(["", f"### {labels['raw_tables']}"])
        for table in evidence.tables:
            sections.extend(
                [
                    "",
                    f"#### {table.title}",
                    "",
                    f"- {labels['table']}: `{table.id}`",
                    f"- {labels['purpose']}: {table.purpose}",
                    f"- {labels['rows']}: `{len(table.rows)}`",
                    f"- {labels['raw_data']}: `{table_path_prefix}/{table.id}.csv`",
                    f"- {labels['evidence']}: "
                    + _render_canonical_refs(table.evidence_refs, labels),
                ]
            )
        sections.extend(["", f"### {labels['evidence_items']}"])
    for group in group_evidence_by_content(evidence.items):
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
            sections.extend(["", f"#### {labels['content']}", "", group.content])
        elif group.content:
            sections.extend(["", f"_{labels['raw_table_available']}_"])
        sections.extend(
            [
                "",
                f"#### {labels['audit_records']}",
                "",
                "```json",
                json.dumps(
                    [
                        grouped_item.model_dump(mode="json", exclude={"content"})
                        for grouped_item in group.items
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
            ]
        )


def render_run_export_package(run_export: RunExport) -> bytes:
    """Build a self-verifying ZIP with a readable report and raw audit data."""

    payloads: dict[str, bytes] = {
        "report.md": render_run_export_markdown(run_export).encode(),
        "run.json": _json_bytes(
            {
                "schema_version": run_export.schema_version,
                "run": run_export.run.model_dump(mode="json"),
                "result": run_export.result.model_dump(mode="json"),
                "research_node": (
                    run_export.research_node.model_dump(mode="json")
                    if run_export.research_node is not None
                    else None
                ),
                "attempts": [attempt.model_dump(mode="json") for attempt in run_export.attempts],
                "incremental_context": (
                    run_export.incremental_context.model_dump(mode="json")
                    if run_export.incremental_context is not None
                    else None
                ),
            }
        ),
        "artifacts.json": _json_bytes(
            [artifact.model_dump(mode="json") for artifact in run_export.artifacts]
        ),
        "evidence.json": _json_bytes(
            run_export.evidence.model_dump(mode="json") if run_export.evidence is not None else None
        ),
    }
    if run_export.evidence is not None:
        for table in run_export.evidence.tables:
            payloads[f"tables/{table.id}.csv"] = _evidence_table_csv(table)
    if run_export.incremental_context is not None:
        baseline_evidence = run_export.incremental_context.full_baseline_evidence
        payloads["baseline/evidence.json"] = _json_bytes(baseline_evidence.model_dump(mode="json"))
        for table in baseline_evidence.tables:
            payloads[f"baseline/tables/{table.id}.csv"] = _evidence_table_csv(table)

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


def _evidence_aliases(*evidence_bundles: Any) -> dict[str, str]:
    items = tuple(
        item for evidence in evidence_bundles if evidence is not None for item in evidence.items
    )
    aliases: dict[str, str] = {}
    for index, group in enumerate(group_evidence_by_content(items), 1):
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
    lines = [
        f"> {labels['opinion_notice']}",
        "",
        f"- {labels['rating']}: **{content.rating.value}**",
        (
            f"- {labels['confidence']}: `"
            f"{labels.enum_name('confidence_level', content.confidence.value)}`"
        ),
        f"- {labels['time_horizon']}: {content.time_horizon}",
        f"- {labels['evidence']}: {_render_refs(content.evidence_refs, labels)}",
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
                    "",
                    level.interpretation,
                ]
            )
    else:
        lines.extend(["", f"_{labels['no_market_references']}_"])
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
