"""Durable Research Revision export rendering."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .serialization import zip_bytes

if TYPE_CHECKING:
    from ..research import RevisionExport


def render_revision_export_markdown(export: RevisionExport) -> str:
    revision = export.revision
    state = revision.current_state
    lines = [
        f"# Research Revision: {state.instrument}",
        "",
        f"- Chain: `{revision.chain_id}`",
        f"- Revision: `{revision.id}`",
        f"- Research Cutoff: {revision.cutoff.isoformat()}",
        "- Information Frontier: "
        + (
            revision.information_frontier.isoformat()
            if revision.information_frontier is not None
            else "not recorded"
        ),
        f"- Language: {state.language}",
        f"- Revision role: {revision.role.value}",
        f"- Execution strategy: {revision.execution_strategy.value}",
        "- Change conclusion: "
        + (
            revision.change_conclusion.value
            if revision.change_conclusion is not None
            else "not applicable"
        ),
        "- Forward Research Anchor: "
        + (
            "qualified"
            if export.chain.forward_research_anchor.is_forward_research_anchor
            else "not qualified"
        ),
        "- Anchor qualification reasons: "
        + (
            ", ".join(item.value for item in export.chain.forward_research_anchor.reasons) or "none"
        ),
        "",
        "## Current Research Opinion",
        "",
        f"**{state.opinion.rating.value}** ({state.opinion.confidence.value})",
        "",
        state.opinion.thesis,
        "",
        "## Update Summary",
        "",
        revision.update_summary.summary,
        "",
        "## Research Claims",
        "",
    ]
    for claim in state.claims:
        refs = ", ".join(claim.evidence_refs)
        lines.append(
            f"- `{claim.id}` [{claim.standing.value}/{claim.confidence.value}] "
            f"{claim.statement} (Evidence: {refs})"
        )
    lines.extend(["", "## Research Questions", ""])
    for question in state.questions:
        successor = (
            f"; successor: `{question.successor_question_id}`"
            if question.successor_question_id is not None
            else ""
        )
        evidence_refs = ", ".join(question.evidence_refs) or "none"
        disposition = (
            f"; disposition: {question.last_disposition.value}"
            if question.last_disposition is not None
            else ""
        )
        reason = (
            f"; reason: {question.disposition_reason}"
            if question.disposition_reason is not None
            else ""
        )
        lines.append(
            f"- `{question.id}` [{question.status.value}] {question.question} "
            f"(Evidence: {evidence_refs}{disposition}{reason}{successor})"
        )
    if revision.delta.question_disposition is not None:
        question_audit = revision.delta.question_disposition
        lines.extend(
            [
                "",
                "### Question Disposition Audit",
                "",
                f"- Status: {question_audit.status}",
                f"- Limitation reason: "
                f"{question_audit.limitation_reason.value if question_audit.limitation_reason is not None else 'none'}",
                f"- Repair attempted: {str(question_audit.repair_attempted).lower()}",
            ]
        )
        for item in question_audit.dispositions:
            successor = (
                f"; successor: `{item.successor_question_id}`"
                if item.successor_question_id is not None
                else ""
            )
            candidate = (
                f"; candidate: `{item.candidate_question_id}`"
                if item.candidate_question_id is not None
                else ""
            )
            lines.append(
                f"- `{item.baseline_question_id}`: {item.disposition.value}{candidate}"
                f"{successor}; Evidence: {', '.join(item.evidence_refs)}; {item.reason}"
            )
    lines.extend(["", "## Scenarios", ""])
    for scenario in state.scenarios:
        lines.append(
            f"- **{scenario.kind.value}** ({scenario.likelihood.value}; "
            f"{scenario.horizon}): {scenario.outcome}"
        )
    for title, factors in (
        ("Risks", state.risks),
        ("Catalysts", state.catalysts),
        ("Invalidation Conditions", state.invalidation_conditions),
    ):
        lines.extend(["", f"## {title}", ""])
        lines.extend(f"- {factor.statement}" for factor in factors)
    lines.extend(["", "## Coverage", ""])
    lines.append("### Anchor Coverage")
    lines.append("")
    for capability in export.chain.forward_research_anchor.capabilities:
        lines.append(
            f"- {capability.capability.value}: required={str(capability.required).lower()}; "
            f"satisfied={str(capability.satisfied).lower()}; sources: "
            f"{', '.join(capability.sources) or 'none'}"
        )
    lines.extend(["", "### Domain Coverage", ""])
    for domain in revision.coverage.domains:
        limitation = "; ".join(domain.limitations) or "none"
        lines.append(f"- {domain.domain}: {domain.status.value}; limitations: {limitation}")
    lines.extend(["", "### Claim Coverage", ""])
    for item in revision.coverage.claims:
        limitation = "; ".join(item.limitations) or "none"
        lines.append(f"- `{item.object_id}`: {item.status.value}; limitations: {limitation}")
    lines.extend(["", "### Question Coverage", ""])
    for item in revision.coverage.questions:
        limitation = "; ".join(item.limitations) or "none"
        lines.append(f"- `{item.object_id}`: {item.status.value}; limitations: {limitation}")
    lines.extend(["", "## Source Watermarks", ""])
    for watermark in revision.evidence_snapshot.source_watermarks:
        limitation = "; ".join(watermark.limitations) or "none"
        overlap = (
            f"; baseline: {watermark.baseline_cutoff}; overlap starts: {watermark.overlap_start}"
            if watermark.baseline_cutoff is not None
            else ""
        )
        lines.append(
            f"- {watermark.source}: {watermark.scanned_start} to {watermark.scanned_end}; "
            f"status: {watermark.status.value}; returned/reported: "
            f"{watermark.returned_records}/{watermark.reported_records}; "
            f"source frontier: "
            f"{watermark.information_frontier.isoformat() if watermark.information_frontier else 'not recorded'}; "
            f"limitations: {limitation}{overlap}"
        )
        for structured in watermark.structured_limitations:
            observed = (
                ", ".join(
                    f"{interval.start} to {interval.end}"
                    for interval in structured.observed_intervals
                )
                or "none"
            )
            lines.append(
                f"  - {structured.kind}/{structured.temporal_scope}; requested: "
                f"{structured.requested_interval.start} to "
                f"{structured.requested_interval.end}; observed: {observed}; "
                f"{structured.presentation_text}"
            )
    lines.extend(["", "## Source Record Versions", ""])
    source_lineage = {
        item.version_id: item for item in revision.evidence_snapshot.source_record_lineage
    }
    for record in revision.evidence_snapshot.source_records:
        item = source_lineage[record.version_id]
        lines.append(
            f"- `{record.version_id}` ({record.source} `{record.record_id}`): "
            f"{record.status.value}; {item.lineage}; observed now: "
            f"{str(item.observed_in_execution).lower()}; available: "
            f"{record.available_at.isoformat()}"
            f" ({record.availability_basis or 'source timestamp'}); native record: "
            f"{record.native_record_id or 'not recorded'}; adjustment: "
            f"{record.adjustment or 'not applicable'}; unit/precision: "
            f"{record.unit or 'not recorded'}/{record.precision if record.precision is not None else 'not recorded'}; "
            f"fallback: {str(record.fallback).lower()}; {record.title}"
        )
    lines.extend(["", "## Fundamental and Market Change Signals", ""])
    for signal in revision.delta.change_signals:
        values = (
            f"; values: {signal.previous_value} -> {signal.current_value}"
            if signal.previous_value is not None or signal.current_value is not None
            else ""
        )
        boundary = (
            f"; boundary: {signal.boundary_label} ({signal.boundary_value})"
            if signal.boundary_label is not None
            else ""
        )
        lines.append(
            f"- `{signal.kind.value}` [{signal.domain}] `{signal.record_id}`; "
            f"requires Full Analysis: {str(signal.requires_full_analysis).lower()}"
            f"{values}{boundary}; {signal.detail}"
        )
    if revision.research_update_audit is not None:
        audit = revision.research_update_audit
        lines.extend(
            [
                "",
                "## Bounded Update Finding",
                "",
                f"- Mode: {audit.mode}",
                f"- Candidate Change Conclusion: "
                f"{audit.candidate.change_conclusion if audit.candidate is not None else 'none'}",
                f"- Authoritative strategy: {audit.authoritative_strategy}",
                f"- Escalation reason: {audit.escalation_reason or 'none'}",
                f"- Comparison: {audit.comparison}",
                *(
                    [
                        "- Comparison explanation: the authoritative Full reassessment was "
                        "Indeterminate, so this result is counted as neither agreement nor "
                        "disagreement."
                    ]
                    if audit.comparison == "inconclusive"
                    else []
                ),
                "- Bounded checked windows: "
                + (
                    "; ".join(
                        f"{item.source} {item.scanned_start} to {item.scanned_end} ({item.status})"
                        for item in audit.checked_windows
                    )
                    or "none"
                ),
                "- Bounded Evidence lineage: "
                + (
                    ", ".join(
                        f"{item.evidence_ref}:{item.lineage}" for item in audit.evidence_lineage
                    )
                    or "none"
                ),
                f"- Bounded work: {audit.bounded_metrics.llm_calls} LLM calls, "
                f"{audit.bounded_metrics.tool_calls} tool calls, "
                f"{audit.bounded_metrics.input_tokens}/"
                f"{audit.bounded_metrics.output_tokens} input/output tokens, "
                f"cost: {audit.bounded_metrics.cost_usd if audit.bounded_metrics.cost_usd is not None else 'not reported'}, "
                f"{audit.bounded_metrics.wall_time_seconds:.3f}s",
                f"- Full work: {audit.full_metrics.llm_calls} LLM calls, "
                f"{audit.full_metrics.tool_calls} tool calls, "
                f"{audit.full_metrics.input_tokens}/"
                f"{audit.full_metrics.output_tokens} input/output tokens, "
                f"cost: {audit.full_metrics.cost_usd if audit.full_metrics.cost_usd is not None else 'not reported'}, "
                f"{audit.full_metrics.wall_time_seconds:.3f}s",
            ]
        )
        if audit.transition_coverage is not None:
            transition = audit.transition_coverage
            lines.extend(
                [
                    "",
                    "### Transition Coverage",
                    "",
                    f"- Frontier interval: ({transition.anchor_frontier.isoformat()}, "
                    f"{transition.update_frontier.isoformat()}]",
                    f"- Complete: {str(transition.complete).lower()}",
                ]
            )
            for capability in transition.capabilities:
                lines.append(
                    f"- {capability.capability}: complete={str(capability.complete).lower()}; "
                    f"sources: {', '.join(capability.sources) or 'none'}"
                )
                for gap in capability.gaps:
                    lines.append(f"  - gap: {gap.start} to {gap.end}")
                for checked in capability.checked_intervals:
                    lines.append(f"  - checked: {checked.start} to {checked.end}")
                for limitation in capability.limitations:
                    observed = (
                        ", ".join(
                            f"{item.start} to {item.end}" for item in limitation.observed_intervals
                        )
                        or "none"
                    )
                    lines.append(
                        f"  - {limitation.scope}/{limitation.kind}/"
                        f"{limitation.temporal_scope}; requested: "
                        f"{limitation.requested_interval.start} to "
                        f"{limitation.requested_interval.end}; observed: {observed}; "
                        f"{limitation.presentation_text}"
                    )
        if audit.semantic_assessment is not None:
            lines.extend(
                [
                    "",
                    "### Semantic Change Assessment",
                    "",
                    f"- Language: {audit.semantic_assessment.language}",
                    f"- Summary: {audit.semantic_assessment.summary}",
                ]
            )
            for relationship in audit.semantic_assessment.relationships:
                targets = (
                    *relationship.suggested_claim_ids,
                    *relationship.suggested_question_ids,
                )
                lines.append(
                    f"- `{relationship.relationship}`; targets: "
                    f"{', '.join(targets) or 'none'}; Evidence: "
                    f"{', '.join(relationship.evidence_refs)}"
                )
    lines.extend(["", "## Effective Evidence Snapshot", ""])
    lineage = {item.evidence_ref: item for item in revision.evidence_snapshot.lineage}
    for evidence_item in revision.evidence_snapshot.bundle.items:
        item_lineage = lineage[evidence_item.ref]
        lines.extend(
            [
                f"### `{evidence_item.ref}`",
                "",
                f"- Lineage: {item_lineage.lineage}",
                f"- Source: {evidence_item.source}",
                f"- Type: {evidence_item.evidence_type}",
                f"- Requested date: {evidence_item.requested_date.isoformat()}",
                f"- Effective date: {evidence_item.effective_date or 'not recorded'}",
                f"- Available at: {evidence_item.available_at or 'not recorded'}",
                "",
                evidence_item.content
                or (
                    str(evidence_item.value)
                    if evidence_item.value is not None
                    else "No readable content recorded."
                ),
                "",
            ]
        )
    metrics = revision.metrics
    lines.extend(
        [
            "## Execution Metrics",
            "",
            f"- LLM calls: {metrics.llm_calls}",
            f"- Tool calls: {metrics.tool_calls}",
            f"- Input tokens: {metrics.input_tokens}",
            f"- Output tokens: {metrics.output_tokens}",
            f"- Cache-hit input tokens: {metrics.cache_hit_input_tokens}",
            f"- Cache-miss input tokens: {metrics.cache_miss_input_tokens}",
            f"- Reasoning output tokens: {metrics.reasoning_output_tokens}",
            f"- Wall time seconds: {metrics.wall_time_seconds}",
        ]
    )
    if export.linked_reports:
        lines.extend(["", "## Linked Full Reports", ""])
        for role, markdown in export.linked_reports.items():
            lines.extend([f"### {role.title()}", "", markdown, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_revision_export_package(export: RevisionExport) -> bytes:
    payloads = {
        "revision.json": export.model_dump_json(indent=2),
        "revision.md": render_revision_export_markdown(export),
        "evidence.json": json.dumps(
            export.revision.evidence_snapshot.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ),
    }
    return zip_bytes(payloads)
