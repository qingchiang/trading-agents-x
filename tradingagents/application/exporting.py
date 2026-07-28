"""Render self-contained, explicit run exports from durable contracts."""

from __future__ import annotations

import json

from .contracts import (
    AnalystReport,
    PerspectiveReview,
    ResearchDecision,
    RunExport,
)


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
        for item in run_export.evidence.items:
            sections.extend(
                [
                    "",
                    f"### `{item.ref}`",
                    "",
                    f"- Source: {item.source}",
                    f"- Type: {item.evidence_type}",
                    f"- Quality: `{item.quality.value}`",
                    f"- Fallback: `{str(item.fallback).lower()}`",
                    "",
                    "```json",
                    json.dumps(
                        item.model_dump(mode="json"),
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
