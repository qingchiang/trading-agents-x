"""Render self-contained, explicit run exports from durable contracts."""

from __future__ import annotations

import json

from .contracts import (
    AnalystReport,
    PerspectiveReview,
    ResearchDecision,
    RunExport,
)
from .evidence import group_evidence_by_content


def render_run_export_markdown(run_export: RunExport) -> str:
    """Render a human-readable audit document without hidden model messages."""
    result = run_export.result
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
    if not run_export.artifacts:
        sections.extend(
            [
                "",
                "_No typed research artifacts were recorded for this run._",
            ]
        )
    for artifact in run_export.artifacts:
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
        narrative = getattr(report, "narrative", str(report))
        sections.extend(["", f"### {name.title()}", "", narrative])

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
    node_names = set(metrics.node_metrics) | set(metrics.node_wall_times)
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
                -(
                    metrics.node_metrics.get(name).wall_time_seconds
                    if name in metrics.node_metrics
                    else metrics.node_wall_times.get(name, 0.0)
                ),
                name,
            ),
        ):
            node_usage = metrics.node_metrics.get(node)
            wall_time = (
                node_usage.wall_time_seconds
                if node_usage is not None
                else metrics.node_wall_times.get(node, 0.0)
            )
            values = (
                (
                    str(node_usage.llm_calls),
                    str(node_usage.tool_calls),
                    str(node_usage.input_tokens),
                    str(node_usage.output_tokens),
                )
                if node_usage is not None
                else ("—", "—", "—", "—")
            )
            sections.append(
                f"| `{node}` | {values[0]} | {values[1]} | "
                f"{values[2]} | {values[3]} | {wall_time:.3f}s |"
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


def _artifact_human_text(
    content: AnalystReport | PerspectiveReview | ResearchDecision,
) -> str:
    if isinstance(content, AnalystReport):
        return content.narrative
    return content.thesis
