"""Bounded collection and semantic assessment for Shadow Research Chain updates."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

from langchain_core.messages import ToolMessage

from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.graph.research_graph import collect_evidence
from tradingagents.graph.structured_output import StructuredOutputError, StructuredOutputRunner
from tradingagents.provenance import (
    ProvenanceRecord,
    SourceInterval,
    SourceWatermark,
    attach_provenance,
    attach_source_observations,
    attach_source_watermarks,
    extract_provenance,
    extract_source_observations,
    extract_source_watermarks,
)

from .contracts import AnalysisRequest, EvidenceBundle, NodeMetrics, RunMetrics
from .evidence import extract_evidence_tables
from .research import (
    ClaimStanding,
    IncrementalEscalationReason,
    IncrementalGateResult,
    QuestionStatus,
    ResearchRevision,
    ResearchRevisionDraft,
    SemanticChangeAssessment,
    SemanticChangeRelationship,
    assess_deterministic_update,
    required_incremental_sources,
)
from .runtime import RunCancelled

_MAX_SEMANTIC_EVIDENCE_ITEMS = 32
_MAX_SEMANTIC_EVIDENCE_TEXT = 1_200
_MAX_SEMANTIC_PROMPT_CHARS = 48_000


def _required_sources(baseline: ResearchRevision) -> set[str]:
    return set(required_incremental_sources(baseline))


def _unavailable_payload(sources: tuple[str, ...], start: str, end: str) -> str:
    payload = attach_source_watermarks(
        "Bounded source collection was unavailable.",
        *(
            SourceWatermark(
                source=source,
                scanned_start=start,
                scanned_end=end,
                status="unavailable",
                limitations=("Bounded collection failed before coverage was established.",),
                requested_interval=SourceInterval(start=start, end=end),
                limitation_kind="unavailable",
            )
            for source in sources
        ),
    )
    return attach_provenance(
        payload,
        *(
            ProvenanceRecord(
                evidence="bounded source collection",
                source=source,
                requested=f"{start} to {end}",
                effective="unavailable",
                timing="unavailable; bounded collection did not establish source frontier provenance",
            )
            for source in sources
        ),
    )


def _sanitize_unattested_sources(
    payload: str,
    *,
    failed_sources: tuple[str, ...],
    start: str,
    end: str,
) -> str:
    failed = set(failed_sources)
    watermarks = tuple(
        item for item in extract_source_watermarks(payload) if item.source not in failed
    )
    observations = tuple(
        item for item in extract_source_observations(payload) if item.source not in failed
    )
    unavailable_payload = _unavailable_payload(failed_sources, start, end)
    unavailable = extract_source_watermarks(unavailable_payload)
    provenance = tuple(item for item in extract_provenance(payload) if item.source not in failed)
    provenance += tuple(extract_provenance(unavailable_payload))
    sanitized = attach_provenance(
        "Bounded source content was omitted because one or more Required sources "
        "did not attest the frozen Information Frontier.",
        *provenance,
    )
    sanitized = attach_source_observations(sanitized, *observations)
    return attach_source_watermarks(sanitized, *watermarks, *unavailable)


def run_deterministic_incremental_gate(
    baseline: ResearchRevision,
    request: AnalysisRequest,
    config: dict[str, Any],
    cancel_requested: Callable[[], bool],
    *,
    information_frontier: datetime,
    on_progress: Callable[[IncrementalGateResult], None] | None = None,
) -> IncrementalGateResult:
    """Collect source-owned Japanese changes without invoking a model."""

    started = monotonic()
    payloads: list[tuple[str, str]] = []
    attempted_sources: set[str] = set()
    tool_calls = 0
    required_sources = _required_sources(baseline)
    overlap_start = (baseline.cutoff - timedelta(days=30)).isoformat()
    cutoff = request.analysis_date.isoformat()

    def collect(name: str, call: Callable[[], str], unavailable_sources: tuple[str, ...]):
        nonlocal tool_calls
        if cancel_requested():
            raise RunCancelled
        tool_calls += 1
        try:
            payload = call()
        except Exception:
            payload = _unavailable_payload(unavailable_sources, overlap_start, cutoff)
        watermarks = extract_source_watermarks(payload)
        attested_sources = {
            watermark.source
            for watermark in watermarks
            if watermark.information_frontier == information_frontier.isoformat()
        }
        required_for_call = required_sources.intersection(unavailable_sources)
        failed_sources = tuple(sorted(required_for_call - attested_sources))
        if not watermarks:
            payload = _unavailable_payload(unavailable_sources, overlap_start, cutoff)
        elif failed_sources:
            payload = _sanitize_unattested_sources(
                payload,
                failed_sources=failed_sources,
                start=overlap_start,
                end=cutoff,
            )
        payloads.append((name, payload))
        attempted_sources.update(unavailable_sources)

    def assess() -> IncrementalGateResult:
        items = []
        for index, (name, payload) in enumerate(payloads):
            items.extend(
                collect_evidence(
                    (
                        ToolMessage(
                            content=payload,
                            name=name,
                            tool_call_id=f"incremental-{index}",
                        ),
                    ),
                    "",
                    requested_date=request.analysis_date,
                    analyst="incremental",
                )
            )
        unique_items = tuple({item.ref: item for item in items}.values())
        bundle = EvidenceBundle(
            instrument=request.ticker,
            analysis_date=request.analysis_date,
            information_frontier=information_frontier,
            items=unique_items,
            tables=extract_evidence_tables(unique_items),
        )
        elapsed = max(0.0, monotonic() - started)
        phase = NodeMetrics(tool_calls=tool_calls, wall_time_seconds=elapsed)
        metrics = RunMetrics(
            tool_calls=tool_calls,
            wall_time_seconds=elapsed,
            node_metrics={"research.incremental.collect": phase},
        )
        result = assess_deterministic_update(
            baseline.id,
            baseline,
            request,
            bundle,
            metrics=metrics,
            mode=config.get("research_update_mode", "off"),
            information_frontier=information_frontier,
        )
        if on_progress is not None:
            on_progress(result)
        return result

    def should_stop(result: IncrementalGateResult) -> bool:
        if result.escalation_reason is None:
            return False
        if result.escalation_reason.value != "coverage_incomplete":
            return True
        return bool(
            result.coverage is not None
            and any(
                domain.requirement.value == "required"
                and domain.source in attempted_sources
                and domain.status.value != "complete"
                for domain in result.coverage.domains
            )
        )

    collect(
        "get_news",
        lambda: route_to_vendor(
            "get_news",
            request.ticker,
            overlap_start,
            cutoff,
            _provenance=True,
            information_frontier=information_frontier.isoformat(),
        ),
        ("EDINET", "TDnet", "Google News"),
    )
    partial = assess()
    if should_stop(partial):
        return partial
    if "fundamentals" in request.analysts or "J-Quants fundamentals" in required_sources:
        collect(
            "get_fundamentals",
            lambda: route_to_vendor(
                "get_fundamentals",
                request.ticker,
                cutoff,
                _provenance=True,
                information_frontier=information_frontier.isoformat(),
            ),
            ("J-Quants fundamentals",),
        )
        partial = assess()
        if should_stop(partial):
            return partial
    if (
        "market" in request.analysts
        or "J-Quants adjusted OHLCV" in required_sources
        or baseline.current_state.market_reference_levels
    ):
        collect(
            "get_verified_market_snapshot",
            lambda: route_to_vendor(
                "get_verified_market_snapshot",
                request.ticker,
                cutoff,
                260,
                _provenance=True,
                information_frontier=information_frontier.isoformat(),
            ),
            ("J-Quants adjusted OHLCV",),
        )
    if cancel_requested():
        raise RunCancelled
    return assess()


def _semantic_evidence_summary(item: Any) -> dict[str, Any]:
    content = item.content
    if content is not None and len(content) > _MAX_SEMANTIC_EVIDENCE_TEXT:
        content = content[:_MAX_SEMANTIC_EVIDENCE_TEXT] + "..."
    return {
        "ref": item.ref,
        "source": item.source,
        "evidence_type": item.evidence_type,
        "effective_date": item.effective_date,
        "available_at": item.available_at,
        "content": content,
        "value": item.value,
        "measurement_kind": item.measurement_kind,
        "unit": item.unit,
        "quality": item.quality,
        "fallback": item.fallback,
    }


def _is_research_context(item: Any) -> bool:
    markers = {
        "prior research",
        "research artifact",
        "research report",
        "old report",
        "deliberation transcript",
    }

    def normalized(value: Any) -> str:
        return " ".join(str(value).replace("_", " ").replace("-", " ").casefold().split())

    labels = {normalized(item.source), normalized(item.evidence_type)}
    for key in ("content_kind", "artifact_kind", "record_kind", "research_kind"):
        value = item.provenance.get(key)
        if value is not None:
            labels.add(normalized(value))
    return bool(labels & markers)


def _without_evidence_refs(value: Any, excluded_refs: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                [item for item in child if item not in excluded_refs]
                if key == "evidence_refs" and isinstance(child, list)
                else _without_evidence_refs(child, excluded_refs)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_without_evidence_refs(item, excluded_refs) for item in value]
    return value


def _semantic_prompt(
    baseline: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft,
) -> tuple[str, tuple[str, ...]]:
    new_refs = tuple(candidate.delta.new_evidence_refs)
    items = {item.ref: item for item in candidate.evidence_snapshot.bundle.items}
    new_evidence = tuple(items[ref] for ref in new_refs if ref in items)
    prior_refs = {
        ref
        for claim in baseline.current_state.claims
        if claim.standing is ClaimStanding.ACTIVE
        for ref in claim.evidence_refs
    }
    prior_refs.update(
        ref
        for question in baseline.current_state.questions
        if question.status in {QuestionStatus.OPEN, QuestionStatus.ANSWERED}
        for ref in question.evidence_refs
    )
    prior_evidence = tuple(
        item
        for item in baseline.evidence_snapshot.bundle.items
        if item.ref in prior_refs and not _is_research_context(item)
    )
    excluded_refs = {
        item.ref for item in baseline.evidence_snapshot.bundle.items if _is_research_context(item)
    }
    if (
        not new_evidence
        or len(new_evidence) > _MAX_SEMANTIC_EVIDENCE_ITEMS
        or len(prior_evidence) > _MAX_SEMANTIC_EVIDENCE_ITEMS
    ):
        raise ValueError("semantic assessment input is empty or exceeds its item bound")
    payload = {
        "current_research_state": _without_evidence_refs(
            baseline.current_state.model_dump(mode="json"),
            excluded_refs,
        ),
        "relevant_claim_ids": tuple(
            claim.id
            for claim in baseline.current_state.claims
            if claim.standing is ClaimStanding.ACTIVE
        ),
        "relevant_question_ids": tuple(
            question.id
            for question in baseline.current_state.questions
            if question.status in {QuestionStatus.OPEN, QuestionStatus.ANSWERED}
        ),
        "materiality_rules": (
            "weakening, contradiction, answering, reopening, unresolved uncertainty, "
            "potentially material novelty, and ordinal confidence changes require Full Analysis",
            "only support or irrelevance with stable identities and confidence may preserve state",
        ),
        "coverage_rules": {
            "supports_no_material_change": candidate.coverage.supports_no_material_change,
            "domains": tuple(
                {
                    "domain": item.domain,
                    "source": item.source,
                    "requirement": item.requirement,
                    "status": item.status,
                    "limitations": item.limitations,
                }
                for item in candidate.coverage.domains
            ),
        },
        "necessary_prior_evidence_summaries": tuple(
            _semantic_evidence_summary(item) for item in prior_evidence
        ),
        "new_evidence": tuple(_semantic_evidence_summary(item) for item in new_evidence),
        "output_language": baseline.current_state.language,
    }
    prompt = (
        "Assess how the new Evidence relates to the Current Research State. "
        "Return only the schema-constrained result. Application code resolves all persistent "
        "identities, so use only the supplied IDs and never create an ID. Human-readable "
        "fields must use output_language.\n\nBOUNDED INPUT:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    )
    if len(prompt) > _MAX_SEMANTIC_PROMPT_CHARS:
        raise ValueError("semantic assessment prompt exceeds its character bound")
    return prompt, tuple(item.ref for item in new_evidence)


def _semantic_escalation_reason(
    baseline: ResearchRevisionDraft,
    assessment: SemanticChangeAssessment,
) -> IncrementalEscalationReason | None:
    claims = {item.id: item for item in baseline.current_state.claims}
    questions = {item.id: item for item in baseline.current_state.questions}
    claim_relationships = {
        SemanticChangeRelationship.SUPPORT,
        SemanticChangeRelationship.WEAKENING,
        SemanticChangeRelationship.CONTRADICTION,
    }
    question_relationships = {
        SemanticChangeRelationship.ANSWERING,
        SemanticChangeRelationship.REOPENING,
    }
    assignments: dict[str, set[tuple[str, str]]] = {}
    for item in assessment.relationships:
        claim_ids = item.suggested_claim_ids
        question_ids = item.suggested_question_ids
        if (
            len(claim_ids) + len(question_ids) > 1
            or any(value not in claims for value in claim_ids)
            or any(value not in questions for value in question_ids)
            or (item.relationship in claim_relationships and (len(claim_ids) != 1 or question_ids))
            or (
                item.relationship in question_relationships
                and (len(question_ids) != 1 or claim_ids)
            )
            or (
                item.relationship is SemanticChangeRelationship.POTENTIALLY_MATERIAL_NOVELTY
                and (claim_ids or question_ids)
            )
            or (item.suggested_claim_confidence is not None and len(claim_ids) != 1)
        ):
            return IncrementalEscalationReason.AMBIGUOUS_IDENTITY
        if item.suggested_claim_confidence is not None:
            claim = claims[claim_ids[0]]
            if item.suggested_claim_confidence is not claim.confidence:
                return IncrementalEscalationReason.CONFIDENCE_CHANGE
        targets = {
            *(("claim", value) for value in claim_ids),
            *(("question", value) for value in question_ids),
        }
        for evidence_ref in item.evidence_refs:
            assignments.setdefault(evidence_ref, set()).update(targets)
    if any(len(targets) > 1 for targets in assignments.values()):
        return IncrementalEscalationReason.AMBIGUOUS_IDENTITY
    relationships = {item.relationship for item in assessment.relationships}
    for relationship, reason in (
        (
            SemanticChangeRelationship.CONTRADICTION,
            IncrementalEscalationReason.SEMANTIC_CONTRADICTION,
        ),
        (
            SemanticChangeRelationship.WEAKENING,
            IncrementalEscalationReason.SEMANTIC_WEAKENING,
        ),
        (
            SemanticChangeRelationship.POTENTIALLY_MATERIAL_NOVELTY,
            IncrementalEscalationReason.POTENTIALLY_MATERIAL_NOVELTY,
        ),
        (
            SemanticChangeRelationship.ANSWERING,
            IncrementalEscalationReason.SEMANTIC_ANSWERING,
        ),
        (
            SemanticChangeRelationship.REOPENING,
            IncrementalEscalationReason.SEMANTIC_REOPENING,
        ),
        (
            SemanticChangeRelationship.UNCERTAINTY,
            IncrementalEscalationReason.SEMANTIC_UNCERTAINTY,
        ),
    ):
        if relationship in relationships:
            return reason
    return None


def assess_semantic_update(
    baseline: ResearchRevisionDraft,
    deterministic: IncrementalGateResult,
    llm: Any,
) -> IncrementalGateResult:
    """Resolve a deterministic quiet candidate through one bounded model contract."""

    candidate = deterministic.candidate
    if candidate is None:
        return deterministic
    new_refs = set(candidate.delta.new_evidence_refs)
    if any(
        item.ref in new_refs and _is_research_context(item)
        for item in candidate.evidence_snapshot.bundle.items
    ):
        return deterministic.model_copy(
            update={
                "candidate": None,
                "escalation_reason": IncrementalEscalationReason.SCHEMA_INVALID,
            }
        )
    try:
        prompt, new_refs = _semantic_prompt(baseline, candidate)
    except ValueError:
        return deterministic.model_copy(
            update={
                "candidate": None,
                "escalation_reason": IncrementalEscalationReason.SEMANTIC_INPUT_OVERSIZE,
            }
        )

    allowed_claim_ids = {item.id for item in baseline.current_state.claims}
    allowed_question_ids = {item.id for item in baseline.current_state.questions}

    def validate(value: SemanticChangeAssessment) -> SemanticChangeAssessment:
        if value.language != baseline.current_state.language:
            raise ValueError("semantic output language differs from Current Research State")
        assessed_refs: set[str] = set()
        for item in value.relationships:
            if not set(item.evidence_refs).issubset(new_refs):
                raise ValueError("semantic relationship used Evidence outside bounded input")
            assessed_refs.update(item.evidence_refs)
            if not set(item.suggested_claim_ids).issubset(allowed_claim_ids):
                # Unknown suggestions are handled as ambiguous identities after validation.
                continue
            if not set(item.suggested_question_ids).issubset(allowed_question_ids):
                continue
        if assessed_refs != set(new_refs):
            raise ValueError("semantic assessment must classify every new Evidence item")
        return value

    example = SemanticChangeAssessment(
        language=baseline.current_state.language,
        summary="The new Evidence supports an existing Claim without changing its confidence.",
        relationships=(
            {
                "evidence_refs": (new_refs[0],),
                "relationship": "support",
                "suggested_claim_ids": (baseline.current_state.claims[0].id,),
            },
        ),
    )
    runner = StructuredOutputRunner(
        llm=llm,
        schema=SemanticChangeAssessment,
        validator=validate,
        node="research.incremental.semantic_assessment",
        invoke_config={"metadata": {"research_node": "research.incremental.semantic_assessment"}},
        repair_mode="preferred",
        include_candidate_in_repair=True,
        candidate_only_repair=True,
        repair_instructions=(
            "Use only supplied Claim, Question, and Evidence identifiers and preserve "
            f"the output language {baseline.current_state.language}."
        ),
    )
    try:
        assessment = runner.invoke(
            prompt,
            example=example.model_dump(mode="json"),
            allowed_evidence_refs=new_refs,
        ).value
    except StructuredOutputError:
        return deterministic.model_copy(
            update={
                "candidate": None,
                "escalation_reason": IncrementalEscalationReason.SEMANTIC_OUTPUT_INVALID,
            }
        )
    reason = _semantic_escalation_reason(baseline, assessment)
    if reason is not None:
        return deterministic.model_copy(
            update={
                "candidate": None,
                "escalation_reason": reason,
                "semantic_assessment": assessment,
            }
        )
    summary = candidate.update_summary.model_copy(update={"summary": assessment.summary})
    return deterministic.model_copy(
        update={
            "candidate": candidate.model_copy(update={"update_summary": summary}),
            "semantic_assessment": assessment,
        }
    )
