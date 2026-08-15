"""Typed collaborators for non-terminal analysis execution work."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver

from tradingagents.dataflows.config import use_config
from tradingagents.graph.research_graph import GraphExecution, ResearchGraph

from .anchor_readiness import AnchorReadinessResult
from .contracts import (
    AnalysisRequest,
    EvidenceBundle,
    MemoryContext,
    ResearchArtifactDraft,
    ResearchUpdateAudit,
    ResearchUpdateCandidate,
    ResearchUpdateSemanticAssessment,
    ResearchUpdateTransitionCoverage,
    RunMetrics,
)
from .incremental import assess_semantic_update, run_deterministic_incremental_gate
from .llms import RunLLMs
from .metrics import MetricsCallback, merge_run_metrics
from .research import (
    IncrementalEscalationReason,
    IncrementalGateResult,
    ResearchRevision,
    ResearchRevisionDraft,
    close_revision_over_update_candidate,
    derive_shadow_comparison,
    validate_experimental_nmc_candidate,
)
from .runtime import RunContext
from .settings import AppSettings, RunSettings


@dataclass(frozen=True, slots=True)
class BoundedUpdateExecution:
    """Completed bounded assessment after semantic and mode-specific validation."""

    result: IncrementalGateResult
    transition_complete: bool


class BoundedUpdateCoordinator:
    """Run bounded and semantic assessment without owning durable lifecycle state."""

    def __init__(
        self,
        *,
        incremental_gate: Callable[..., IncrementalGateResult],
        llm_factory: Callable[..., RunLLMs | tuple[Any, Any]],
    ) -> None:
        self._incremental_gate = incremental_gate
        self._llm_factory = llm_factory

    def execute(
        self,
        baseline: ResearchRevision,
        request: AnalysisRequest,
        *,
        run_settings: RunSettings,
        dataflow_config: dict[str, Any],
        information_frontier: datetime | None,
        cancel_requested: Callable[[], bool],
        transition_is_complete: Callable[[IncrementalGateResult], bool],
        on_progress: Callable[[IncrementalGateResult], object],
    ) -> BoundedUpdateExecution:
        if self._incremental_gate is run_deterministic_incremental_gate:
            result = run_deterministic_incremental_gate(
                baseline,
                request,
                {
                    **dataflow_config,
                    "research_update_mode": run_settings.research_update_mode,
                },
                cancel_requested,
                information_frontier=information_frontier,
                on_progress=on_progress,
            )
            if result.candidate is not None:
                semantic_metrics = MetricsCallback()
                semantic_llms = self._llm_factory(
                    run_settings,
                    callbacks=[semantic_metrics],
                )
                semantic_llm = (
                    semantic_llms.quick_serializer
                    if isinstance(semantic_llms, RunLLMs)
                    else semantic_llms[0]
                )
                result = assess_semantic_update(
                    baseline,
                    result,
                    semantic_llm,
                ).model_copy(
                    update={
                        "metrics": merge_run_metrics(
                            result.metrics,
                            semantic_metrics.snapshot(),
                        )
                    }
                )
        else:
            result = self._incremental_gate(
                baseline,
                request,
                dataflow_config,
                cancel_requested,
            )

        transition_complete = transition_is_complete(result)
        candidate = result.candidate
        if (
            candidate is not None
            and run_settings.research_update_mode == "experimental"
            and not transition_complete
        ):
            result = result.model_copy(
                update={
                    "candidate": None,
                    "escalation_reason": IncrementalEscalationReason.COVERAGE_INCOMPLETE,
                }
            )
            candidate = None
        if candidate is not None and run_settings.research_update_mode == "experimental":
            invalid_reason = validate_experimental_nmc_candidate(baseline, candidate)
            if invalid_reason is not None:
                result = result.model_copy(
                    update={"candidate": None, "escalation_reason": invalid_reason}
                )
        return BoundedUpdateExecution(
            result=result,
            transition_complete=transition_complete,
        )


@dataclass(frozen=True, slots=True)
class ShadowAuditProjection:
    """Audit and authoritative draft produced by one Shadow comparison."""

    audit: ResearchUpdateAudit
    revision_draft: ResearchRevisionDraft


class ResearchUpdateAuditMapper:
    """Single mapper from research-domain results to durable audit contracts."""

    @staticmethod
    def initial() -> ResearchUpdateAudit:
        return ResearchUpdateAudit(comparison="not_applicable")

    @staticmethod
    def from_bounded_result(
        result: IncrementalGateResult,
        *,
        mode: str,
        baseline_information_frontier: datetime | None,
        previous: ResearchUpdateAudit | None,
        transition_complete: bool,
    ) -> ResearchUpdateAudit:
        candidate = result.candidate
        coverage = result.coverage or (candidate.coverage if candidate is not None else None)
        snapshot = result.evidence_snapshot or (
            candidate.evidence_snapshot if candidate is not None else None
        )
        return ResearchUpdateAudit(
            mode="experimental" if mode == "experimental" else "shadow",
            candidate=(
                ResearchUpdateCandidate(
                    change_conclusion=candidate.change_conclusion.value,
                    coverage={
                        **candidate.coverage.model_dump(
                            mode="json", exclude={"anchor_qualification"}
                        ),
                        "schema_version": "1",
                    },
                    update_summary=candidate.update_summary.model_dump(mode="json"),
                    evidence_snapshot=candidate.evidence_snapshot.model_dump(mode="json"),
                )
                if candidate is not None
                else None
            ),
            coverage=(
                {
                    **coverage.model_dump(mode="json", exclude={"anchor_qualification"}),
                    "schema_version": "1",
                }
                if coverage is not None
                else None
            ),
            checked_windows=(
                tuple(item.model_dump(mode="json") for item in snapshot.source_watermarks)
                if snapshot is not None
                else ()
            ),
            transition_coverage=(
                ResearchUpdateTransitionCoverage.model_validate(
                    result.transition_coverage.model_dump(mode="json")
                )
                if result.transition_coverage is not None
                else None
            ),
            evidence_lineage=(
                tuple(item.model_dump(mode="json") for item in snapshot.lineage)
                if snapshot is not None
                else ()
            ),
            semantic_assessment=(
                ResearchUpdateSemanticAssessment.model_validate(
                    result.semantic_assessment.model_dump(mode="json")
                )
                if result.semantic_assessment is not None
                else None
            ),
            baseline_information_frontier=baseline_information_frontier,
            escalation_reason=(
                IncrementalEscalationReason.COVERAGE_INCOMPLETE.value
                if candidate is not None and not transition_complete
                else result.escalation_reason.value
                if result.escalation_reason is not None
                else None
            ),
            comparison="not_applicable",
            bounded_metrics=merge_run_metrics(
                previous.bounded_metrics if previous is not None else RunMetrics(),
                result.metrics,
            ),
            full_metrics=(previous.full_metrics if previous is not None else RunMetrics()),
        )

    @staticmethod
    def with_authoritative_strategy(
        audit: ResearchUpdateAudit,
        strategy: Literal["full", "incremental"],
    ) -> ResearchUpdateAudit:
        return audit.model_copy(update={"authoritative_strategy": strategy})

    @staticmethod
    def with_partial_full_metrics(
        audit: ResearchUpdateAudit,
        metrics: RunMetrics,
    ) -> ResearchUpdateAudit:
        return audit.model_copy(
            update={"full_metrics": merge_run_metrics(audit.full_metrics, metrics)}
        )

    @staticmethod
    def project_shadow(
        audit: ResearchUpdateAudit,
        *,
        incremental_result: IncrementalGateResult,
        revision_draft: ResearchRevisionDraft,
        previous: ResearchUpdateAudit | None,
        current_full_metrics: RunMetrics,
    ) -> ShadowAuditProjection:
        full_metrics = merge_run_metrics(
            previous.full_metrics if previous is not None else RunMetrics(),
            current_full_metrics,
        )
        projected = audit.model_copy(
            update={
                "comparison": derive_shadow_comparison(
                    incremental_result.candidate,
                    revision_draft,
                ),
                "full_metrics": full_metrics,
            }
        )
        draft = ResearchRevisionDraft.model_validate(
            revision_draft.model_copy(
                update={"research_update_audit": projected}
            ).model_dump(mode="python")
        )
        return ShadowAuditProjection(audit=projected, revision_draft=draft)


class ShadowUpdateCoordinator:
    """Close Full Evidence over a bounded candidate and project its comparison."""

    @staticmethod
    def execute(
        audit: ResearchUpdateAudit,
        *,
        incremental_result: IncrementalGateResult,
        revision_draft: ResearchRevisionDraft,
        previous_audit: ResearchUpdateAudit | None,
        current_full_metrics: RunMetrics,
    ) -> ShadowAuditProjection:
        bounded_snapshot = (
            incremental_result.candidate
            if incremental_result.candidate is not None
            else incremental_result.evidence_snapshot
        )
        if bounded_snapshot is not None:
            revision_draft = close_revision_over_update_candidate(
                revision_draft,
                bounded_snapshot,
            )
        return ResearchUpdateAuditMapper.project_shadow(
            audit,
            incremental_result=incremental_result,
            revision_draft=revision_draft,
            previous=previous_audit,
            current_full_metrics=current_full_metrics,
        )


@dataclass(frozen=True, slots=True)
class GraphExecutionResult:
    """Successful graph result plus serializer needed by state assembly."""

    execution: GraphExecution
    quick_serializer_llm: Any
    resumed: bool


class GraphExecutionCoordinator:
    """Construct and execute a graph without writing lifecycle terminal state."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        llm_factory: Callable[..., RunLLMs | tuple[Any, Any]],
        graph_factory: Callable[..., ResearchGraph],
    ) -> None:
        self._settings = settings
        self._llm_factory = llm_factory
        self._graph_factory = graph_factory

    @contextmanager
    def execute(
        self,
        *,
        run_id: str,
        attempt: int,
        request: AnalysisRequest,
        run_settings: RunSettings,
        dataflow_config: dict[str, Any],
        memory: MemoryContext,
        instrument_context: str,
        metrics: MetricsCallback,
        checkpoint_thread: str,
        information_frontier: datetime | None,
        anchor_readiness: AnchorReadinessResult | None,
        cancel_requested: Callable[[], bool],
        shutdown_requested: Callable[[], bool],
        artifact_writer: Callable[[ResearchArtifactDraft], None],
        evidence_writer: Callable[[EvidenceBundle], None],
        sealed_evidence_reader: Callable[[], EvidenceBundle | None],
        event_writer: Callable[[dict[str, Any]], None],
        resumed_writer: Callable[[int], None],
    ) -> Iterator[GraphExecutionResult]:
        with use_config(dataflow_config):
            llms = self._llm_factory(run_settings, callbacks=[metrics])
            if isinstance(llms, RunLLMs):
                quick_llm = llms.quick
                deep_llm = llms.deep
                quick_serializer_llm = llms.quick_serializer
                deep_serializer_llm = llms.deep_serializer
            else:
                quick_llm, deep_llm = llms
                quick_serializer_llm = quick_llm
                deep_serializer_llm = deep_llm
            graph = self._graph_factory(
                quick_llm=quick_llm,
                deep_llm=deep_llm,
                quick_serializer_llm=quick_serializer_llm,
                deep_serializer_llm=deep_serializer_llm,
                profile=request.profile,
                selected_analysts=request.analysts,
                metrics=metrics,
            )
        context = RunContext(
            run_id=run_id,
            request=request,
            settings=run_settings,
            dataflow_config=dataflow_config,
            memory=memory,
            instrument_context=instrument_context,
            cancel_requested=cancel_requested,
            information_frontier=information_frontier,
            anchor_readiness=anchor_readiness,
            shutdown_requested=shutdown_requested,
            artifact_writer=artifact_writer,
            evidence_writer=evidence_writer,
            sealed_evidence_reader=sealed_evidence_reader,
        )
        with SqliteSaver.from_conn_string(str(self._settings.database_path)) as saver:
            saver.conn.execute("PRAGMA journal_mode=WAL")
            saver.conn.execute(f"PRAGMA busy_timeout={self._settings.busy_timeout_ms}")
            saver.setup()
            checkpoint_config = {"configurable": {"thread_id": checkpoint_thread}}
            resumed = saver.get_tuple(checkpoint_config) is not None
            if resumed:
                resumed_writer(attempt)
            with use_config(dataflow_config):
                execution = graph.execute(
                    context,
                    checkpointer=saver,
                    checkpoint_thread_id=checkpoint_thread,
                    resume=resumed,
                    on_event=event_writer,
                )
            if information_frontier is not None:
                execution = replace(
                    execution,
                    evidence=EvidenceBundle.model_validate(
                        {
                            **execution.evidence.model_dump(mode="python"),
                            "information_frontier": information_frontier,
                            "digest": None,
                        }
                    ),
                )
            yield GraphExecutionResult(
                execution=execution,
                quick_serializer_llm=quick_serializer_llm,
                resumed=resumed,
            )
            saver.delete_thread(checkpoint_thread)
