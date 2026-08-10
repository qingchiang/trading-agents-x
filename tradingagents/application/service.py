"""Application service owning the complete analysis lifecycle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    resolve_instrument_identity,
)
from tradingagents.dataflows.config import use_config
from tradingagents.dataflows.interface import validate_market_routing
from tradingagents.dataflows.symbol_utils import (
    match_exchange_suffix,
    normalize_symbol,
)
from tradingagents.graph.research_graph import GraphExecution, ResearchGraph
from tradingagents.persistence import upgrade_database

from .contracts import (
    AnalysisRequest,
    AnalysisResult,
    AnalystReport,
    EvidenceBundle,
    MemoryContext,
    ResearchArtifactDraft,
    ResearchUpdateAudit,
    ResearchUpdateCandidate,
    ResearchUpdateSemanticAssessment,
    RunEvent,
    RunExport,
    RunMetrics,
    RunStatus,
)
from .exporting import (
    render_run_export_markdown,
    render_run_export_package,
)
from .incremental import assess_semantic_update, run_deterministic_incremental_gate
from .instrument_names import resolve_local_instrument_name
from .llms import RunLLMs, create_run_llms
from .metrics import MetricsCallback, merge_run_metrics
from .question_disposition import run_full_question_disposition
from .repository import InvalidResearchBaselineError, RunRepository, RunView
from .research import (
    IncrementalGateResult,
    ResearchChain,
    ResearchExecutionStrategy,
    ResearchRevision,
    ResearchRevisionDraft,
    RevisionExport,
    assemble_full_revision,
    assemble_full_update,
    close_revision_over_update_candidate,
    derive_shadow_comparison,
    evaluate_next_update_policy,
    prepare_experimental_nmc_revision,
    render_revision_export_markdown,
    render_revision_export_package,
    validate_experimental_nmc_candidate,
)
from .runtime import RunCancelled, RunContext, WorkerShutdown
from .settings import AppSettings, RunSettings

logger = logging.getLogger(__name__)

EventHandler = Callable[[RunEvent], None]


class ChainUpdateExecutionError(RuntimeError):
    """A synchronous Chain update failed after its durable run was created."""

    def __init__(self, run_id: str):
        super().__init__("Research Chain update execution failed")
        self.run_id = run_id


def _segment_metrics(
    incremental_result: IncrementalGateResult | None,
    update_audit: ResearchUpdateAudit | None,
    metrics: MetricsCallback,
):
    full_metrics = metrics.snapshot()
    bounded_metrics = (
        incremental_result.metrics
        if incremental_result is not None
        else update_audit.bounded_metrics
        if update_audit is not None
        else None
    )
    return merge_run_metrics(bounded_metrics, full_metrics) if bounded_metrics else full_metrics


def _instrument_display_name(identity: Any) -> str | None:
    if not isinstance(identity, dict):
        return None
    for key in ("short_name", "company_name", "long_name", "name"):
        value = identity.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized.casefold() not in {
            "none",
            "n/a",
            "nan",
            "null",
        }:
            return normalized[:300]
    return None


class AnalysisService:
    """The only component allowed to coordinate graph and durable state."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        repository: RunRepository | None = None,
        llm_factory: Callable[..., RunLLMs | tuple[Any, Any]] = (create_run_llms),
        graph_factory: Callable[..., ResearchGraph] = ResearchGraph,
        identity_resolver: Callable[..., dict[str, str]] = resolve_instrument_identity,
        local_name_resolver: Callable[[str, str, dict[str, Any]], str | None] = (
            resolve_local_instrument_name
        ),
        state_assembler: Callable[
            [AnalysisRequest, GraphExecution], ResearchRevisionDraft
        ] = assemble_full_revision,
        revision_comparator: Callable[
            [str, ResearchRevision, ResearchRevisionDraft], ResearchRevisionDraft
        ] = assemble_full_update,
        question_dispositioner: Callable[
            [ResearchRevision, ResearchRevisionDraft, Any], ResearchRevisionDraft
        ] = run_full_question_disposition,
        incremental_gate: Callable[
            [ResearchRevision, AnalysisRequest, dict[str, Any], Callable[[], bool]],
            IncrementalGateResult,
        ]
        | None = None,
    ):
        self.settings = settings
        if repository is None:
            upgrade_database(settings)
        self.repository = repository or RunRepository(settings)
        self.llm_factory = llm_factory
        self.graph_factory = graph_factory
        self.identity_resolver = identity_resolver
        self.local_name_resolver = local_name_resolver
        self.state_assembler = state_assembler
        self.revision_comparator = revision_comparator
        self.question_dispositioner = question_dispositioner
        self.incremental_gate = incremental_gate or run_deterministic_incremental_gate

    def _present_chain(self, chain: ResearchChain) -> ResearchChain:
        revision = chain.current_revision
        if revision is None:
            return chain
        evaluation = evaluate_next_update_policy(
            revision,
            instrument=chain.instrument,
            mode=self.settings.research_update_mode,
        )
        return chain.model_copy(
            update={
                "next_update_policy": evaluation.policy,
                "next_update_reason": evaluation.reason,
            }
        )

    def get_research_chain(self, chain_id: str) -> ResearchChain:
        return self._present_chain(self.repository.get_research_chain(chain_id))

    def list_research_chains(self, *, instrument: str | None = None) -> list[ResearchChain]:
        return [
            self._present_chain(chain)
            for chain in self.repository.list_research_chains(instrument=instrument)
        ]

    def enqueue(
        self,
        request: AnalysisRequest,
        *,
        idempotency_key: str | None = None,
        source_run_id: str | None = None,
        research_chain_requested: bool = False,
    ) -> RunView:
        run_settings = self.settings.resolve_run(request)
        request = self.settings.materialize_request(
            request,
            run_settings=run_settings,
        )
        view, created = self.repository.create_run(
            request,
            run_settings.snapshot(),
            idempotency_key=idempotency_key,
            source_run_id=source_run_id,
            research_chain_requested=research_chain_requested,
        )
        if created:
            self.repository.append_event(
                view.id,
                "run.queued",
                payload={
                    "profile": request.profile.value,
                    "ticker": request.ticker,
                    "source_run_id": source_run_id,
                    "research_chain_requested": research_chain_requested,
                },
            )
        return view

    def enqueue_initial_chain(
        self,
        request: AnalysisRequest,
        *,
        idempotency_key: str | None = None,
    ) -> RunView:
        return self.enqueue(
            request,
            idempotency_key=idempotency_key,
            research_chain_requested=True,
        )

    def enqueue_chain_update(
        self,
        chain_id: str,
        baseline_revision_id: str,
        request: AnalysisRequest,
        *,
        idempotency_key: str | None = None,
        execution_strategy: ResearchExecutionStrategy | None = None,
    ) -> RunView:
        run_settings = self.settings.resolve_run(request)
        request = self.settings.materialize_request(request, run_settings=run_settings)
        baseline = self.repository.get_research_revision(baseline_revision_id)
        evaluation = evaluate_next_update_policy(
            baseline,
            instrument=request.ticker,
            mode=run_settings.research_update_mode,
        )
        if (
            execution_strategy is ResearchExecutionStrategy.INCREMENTAL
            and evaluation.policy != "incremental_allowed"
        ):
            raise InvalidResearchBaselineError(
                "Eligible Baseline does not allow Incremental Execution: "
                f"{evaluation.reason.value if evaluation.reason is not None else 'full_required'}"
            )
        selected_strategy = execution_strategy or (
            ResearchExecutionStrategy.INCREMENTAL
            if evaluation.policy == "incremental_allowed"
            else ResearchExecutionStrategy.FULL
        )
        view, created = self.repository.create_chain_update(
            chain_id,
            baseline_revision_id,
            request,
            run_settings.snapshot(),
            execution_strategy=selected_strategy,
            idempotency_key=idempotency_key,
        )
        if created:
            self.repository.append_event(
                view.id,
                "run.queued",
                payload={
                    "profile": request.profile.value,
                    "ticker": request.ticker,
                    "update_intent_id": view.update_intent_id,
                    "research_chain_id": chain_id,
                    "baseline_revision_id": baseline_revision_id,
                    "execution_strategy": view.research_execution_strategy,
                },
            )
        return view

    def run(
        self,
        request: AnalysisRequest,
        *,
        on_event: EventHandler | None = None,
    ) -> AnalysisResult:
        view = self.enqueue(request)
        worker_id = f"python:{uuid4()}"
        claimed = self.repository.claim_run(
            view.id,
            worker_id,
            self.settings.lease_seconds,
        )
        return self.execute_claimed(
            claimed,
            worker_id=worker_id,
            on_event=on_event,
        )

    def run_initial_chain(
        self,
        request: AnalysisRequest,
        *,
        on_event: EventHandler | None = None,
    ) -> AnalysisResult:
        view = self.enqueue_initial_chain(request)
        worker_id = f"python:{uuid4()}"
        claimed = self.repository.claim_run(
            view.id,
            worker_id,
            self.settings.lease_seconds,
        )
        return self.execute_claimed(
            claimed,
            worker_id=worker_id,
            on_event=on_event,
        )

    def run_chain_update(
        self,
        chain_id: str,
        baseline_revision_id: str,
        request: AnalysisRequest,
        *,
        idempotency_key: str | None = None,
        on_event: EventHandler | None = None,
    ) -> tuple[RunView, AnalysisResult]:
        """Own one synchronous Research Chain update from enqueue through completion."""
        view = self.enqueue_chain_update(
            chain_id,
            baseline_revision_id,
            request,
            idempotency_key=idempotency_key,
        )
        worker_id = f"python-chain-update:{uuid4()}"
        try:
            claimed = self.repository.claim_run(
                view.id,
                worker_id,
                self.settings.lease_seconds,
            )
            result = self.execute_claimed(
                claimed,
                worker_id=worker_id,
                on_event=on_event,
            )
        except Exception as exc:
            raise ChainUpdateExecutionError(view.id) from exc
        return view, result

    def execute_claimed(
        self,
        run: RunView,
        *,
        worker_id: str,
        on_event: EventHandler | None = None,
        shutdown_requested: Callable[[], bool] | None = None,
    ) -> AnalysisResult:
        if run.status is not RunStatus.RUNNING:
            raise ValueError(f"run {run.id} must be claimed before execution")
        run_settings = RunSettings.model_validate(run.config_snapshot)
        dataflow_config = run_settings.dataflow_config(self.settings)
        validate_market_routing(dataflow_config)
        checkpoint_thread = self.repository.checkpoint_thread(run.id)
        self._emit(
            run.id,
            "run.started",
            payload={"attempt": run.attempt},
            on_event=on_event,
        )
        metrics = MetricsCallback()
        incremental_result: IncrementalGateResult | None = None
        update_audit: ResearchUpdateAudit | None = None
        prior_update_audit = run.research_update_audit
        instrument_name = run.instrument_name
        instrument_local_name = run.instrument_local_name

        def persist_incremental_audit(
            result: IncrementalGateResult,
        ) -> ResearchUpdateAudit:
            nonlocal update_audit
            candidate = result.candidate
            bounded_coverage = result.coverage or (
                candidate.coverage if candidate is not None else None
            )
            bounded_snapshot = result.evidence_snapshot or (
                candidate.evidence_snapshot if candidate is not None else None
            )
            update_audit = ResearchUpdateAudit(
                mode=(
                    "experimental"
                    if run_settings.research_update_mode == "experimental"
                    else "shadow"
                ),
                candidate=(
                    ResearchUpdateCandidate(
                        change_conclusion=candidate.change_conclusion.value,
                        coverage=candidate.coverage.model_dump(mode="json"),
                        update_summary=candidate.update_summary.model_dump(mode="json"),
                        evidence_snapshot=candidate.evidence_snapshot.model_dump(mode="json"),
                    )
                    if candidate is not None
                    else None
                ),
                coverage=(
                    bounded_coverage.model_dump(mode="json")
                    if bounded_coverage is not None
                    else None
                ),
                checked_windows=(
                    tuple(
                        item.model_dump(mode="json") for item in bounded_snapshot.source_watermarks
                    )
                    if bounded_snapshot is not None
                    else ()
                ),
                evidence_lineage=(
                    tuple(item.model_dump(mode="json") for item in bounded_snapshot.lineage)
                    if bounded_snapshot is not None
                    else ()
                ),
                semantic_assessment=(
                    ResearchUpdateSemanticAssessment.model_validate(
                        result.semantic_assessment.model_dump(mode="json")
                    )
                    if result.semantic_assessment is not None
                    else None
                ),
                escalation_reason=(
                    result.escalation_reason.value if result.escalation_reason is not None else None
                ),
                comparison="not_applicable",
                bounded_metrics=merge_run_metrics(
                    (
                        prior_update_audit.bounded_metrics
                        if prior_update_audit is not None
                        else RunMetrics()
                    ),
                    result.metrics,
                ),
                full_metrics=(
                    prior_update_audit.full_metrics
                    if prior_update_audit is not None
                    else RunMetrics()
                ),
            )
            self.repository.set_research_update_audit(run.id, update_audit)
            return update_audit

        def persist_partial_full_metrics() -> None:
            nonlocal update_audit
            if update_audit is None:
                return
            update_audit = update_audit.model_copy(
                update={
                    "full_metrics": merge_run_metrics(
                        update_audit.full_metrics,
                        metrics.snapshot(),
                    )
                }
            )
            self.repository.set_research_update_audit(run.id, update_audit)

        with self._heartbeat(run.id, worker_id):
            try:
                with use_config(dataflow_config):
                    try:
                        identity = self.identity_resolver(
                            run.request.ticker,
                            run.request.analysis_date.isoformat(),
                        )
                    except Exception as exc:
                        logger.warning(
                            "instrument identity resolution failed for run %s: %s",
                            run.id,
                            type(exc).__name__,
                        )
                        identity = {}
                    resolved_name = _instrument_display_name(identity)
                    if resolved_name is not None:
                        instrument_name = resolved_name
                        self.repository.set_instrument_name(
                            run.id,
                            resolved_name,
                        )
                    if instrument_local_name is None:
                        try:
                            resolved_local_name = self.local_name_resolver(
                                run.request.ticker,
                                run.request.analysis_date.isoformat(),
                                dataflow_config,
                            )
                        except Exception as exc:
                            logger.warning(
                                "local instrument name resolution failed for run %s: %s",
                                run.id,
                                type(exc).__name__,
                            )
                        else:
                            if resolved_local_name is not None:
                                instrument_local_name = resolved_local_name
                                self.repository.set_instrument_local_name(
                                    run.id,
                                    resolved_local_name,
                                )
                    instrument_context = build_instrument_context(
                        run.request.ticker,
                        identity,
                    )
                    if run.research_execution_strategy == "incremental":
                        if run.baseline_revision_id is None:
                            raise ValueError("Research Chain update has no baseline")
                        baseline = self.repository.get_research_revision(run.baseline_revision_id)
                        update_audit = ResearchUpdateAudit(
                            comparison="not_applicable",
                        )
                        self.repository.set_research_update_audit(run.id, update_audit)
                        if self.incremental_gate is run_deterministic_incremental_gate:
                            incremental_result = run_deterministic_incremental_gate(
                                baseline,
                                run.request,
                                {
                                    **dataflow_config,
                                    "research_update_mode": run_settings.research_update_mode,
                                },
                                lambda: self.repository.cancel_requested(run.id),
                                on_progress=persist_incremental_audit,
                            )
                            if incremental_result.candidate is not None:
                                semantic_metrics = MetricsCallback()
                                semantic_llms = self.llm_factory(
                                    run_settings,
                                    callbacks=[semantic_metrics],
                                )
                                semantic_llm = (
                                    semantic_llms.quick_serializer
                                    if isinstance(semantic_llms, RunLLMs)
                                    else semantic_llms[0]
                                )
                                incremental_result = assess_semantic_update(
                                    baseline,
                                    incremental_result,
                                    semantic_llm,
                                ).model_copy(
                                    update={
                                        "metrics": merge_run_metrics(
                                            incremental_result.metrics,
                                            semantic_metrics.snapshot(),
                                        )
                                    }
                                )
                        else:
                            incremental_result = self.incremental_gate(
                                baseline,
                                run.request,
                                dataflow_config,
                                lambda: self.repository.cancel_requested(run.id),
                            )
                        candidate = incremental_result.candidate
                        if (
                            candidate is not None
                            and run_settings.research_update_mode == "experimental"
                        ):
                            invalid_reason = validate_experimental_nmc_candidate(
                                baseline,
                                candidate,
                            )
                            if invalid_reason is not None:
                                incremental_result = incremental_result.model_copy(
                                    update={
                                        "candidate": None,
                                        "escalation_reason": invalid_reason,
                                    }
                                )
                                candidate = None
                        update_audit = persist_incremental_audit(incremental_result)
                        self._emit(
                            run.id,
                            "research.incremental_assessed",
                            payload={
                                "candidate_outcome": (
                                    candidate.change_conclusion.value
                                    if candidate is not None
                                    else None
                                ),
                                "escalation_reason": update_audit.escalation_reason,
                                "metrics": incremental_result.metrics.model_dump(mode="json"),
                                "checked_windows": update_audit.checked_windows,
                                "coverage": (
                                    update_audit.coverage.model_dump(mode="json")
                                    if update_audit.coverage is not None
                                    else None
                                ),
                                "evidence_lineage": tuple(
                                    item.model_dump(mode="json")
                                    for item in update_audit.evidence_lineage
                                ),
                                "candidate_update_summary": (
                                    update_audit.candidate.update_summary.model_dump(mode="json")
                                    if update_audit.candidate is not None
                                    else None
                                ),
                                "semantic_assessment": (
                                    update_audit.semantic_assessment.model_dump(mode="json")
                                    if update_audit.semantic_assessment is not None
                                    else None
                                ),
                            },
                            on_event=on_event,
                        )
                        if (
                            run_settings.research_update_mode == "experimental"
                            and candidate is not None
                        ):
                            if self.repository.cancel_requested(run.id):
                                raise RunCancelled("cancelled before experimental NMC commit")
                            update_audit = update_audit.model_copy(
                                update={"authoritative_strategy": "incremental"}
                            )
                            self.repository.set_research_update_audit(run.id, update_audit)
                            revision_draft = prepare_experimental_nmc_revision(
                                baseline,
                                candidate,
                                update_audit,
                            )
                            evidence = revision_draft.evidence_snapshot.bundle
                            self._persist_evidence(run.id, evidence, on_event)
                            result = AnalysisResult(
                                run_id=run.id,
                                status=RunStatus.SUCCEEDED,
                                instrument=evidence.instrument,
                                instrument_name=instrument_name,
                                instrument_local_name=instrument_local_name,
                                reports={},
                                decision=None,
                                evidence=evidence,
                                metrics=incremental_result.metrics,
                                recoveries=self.repository.list_recoveries(run.id),
                                warnings=(),
                            )
                            aggregate_metrics = self.repository.complete(
                                run.id,
                                result,
                                evidence=evidence,
                                benchmark=self._benchmark(run.request.ticker, dataflow_config),
                                revision_draft=revision_draft,
                            )
                            result = result.model_copy(update={"metrics": aggregate_metrics})
                            self._emit(
                                run.id,
                                "research.experimental_nmc_committed",
                                payload={
                                    "authoritative_strategy": "incremental",
                                    "outcome": "no_material_change",
                                    "metrics": aggregate_metrics.model_dump(mode="json"),
                                },
                                on_event=on_event,
                            )
                            self._emit(
                                run.id,
                                "run.succeeded",
                                payload={"metrics": aggregate_metrics.model_dump(mode="json")},
                                on_event=on_event,
                            )
                            return result
                        self._emit(
                            run.id,
                            (
                                "research.full_escalation_started"
                                if run_settings.research_update_mode == "experimental"
                                else "research.shadow_full_started"
                            ),
                            payload={
                                "authoritative_strategy": "full",
                                "escalation_reason": update_audit.escalation_reason,
                            },
                            on_event=on_event,
                        )
                        metrics = MetricsCallback()
                    if run.research_chain_requested or run.research_chain_id:
                        memory = MemoryContext(
                            instrument=run.request.ticker,
                            market=self.repository.market_bucket(run.request.ticker),
                        )
                    else:
                        memory = self.repository.memory_context(
                            run.request.ticker,
                            run.request.asset_type.value,
                        )
                    llms = self.llm_factory(
                        run_settings,
                        callbacks=[metrics],
                    )
                    if isinstance(llms, RunLLMs):
                        quick_llm = llms.quick
                        deep_llm = llms.deep
                        quick_serializer_llm = llms.quick_serializer
                        deep_serializer_llm = llms.deep_serializer
                    else:
                        quick_llm, deep_llm = llms
                        quick_serializer_llm = quick_llm
                        deep_serializer_llm = deep_llm
                graph = self.graph_factory(
                    quick_llm=quick_llm,
                    deep_llm=deep_llm,
                    quick_serializer_llm=quick_serializer_llm,
                    deep_serializer_llm=deep_serializer_llm,
                    profile=run.request.profile,
                    selected_analysts=run.request.analysts,
                    metrics=metrics,
                )
                context = RunContext(
                    run_id=run.id,
                    request=run.request,
                    settings=run_settings,
                    dataflow_config=dataflow_config,
                    memory=memory,
                    instrument_context=instrument_context,
                    cancel_requested=lambda: self.repository.cancel_requested(run.id),
                    shutdown_requested=shutdown_requested or (lambda: False),
                    artifact_writer=lambda artifact: self._persist_artifact(
                        run.id,
                        artifact,
                        on_event,
                    ),
                    evidence_writer=lambda evidence: self._persist_evidence(
                        run.id,
                        evidence,
                        on_event,
                    ),
                )
                with SqliteSaver.from_conn_string(str(self.settings.database_path)) as saver:
                    saver.conn.execute("PRAGMA journal_mode=WAL")
                    saver.conn.execute(f"PRAGMA busy_timeout={self.settings.busy_timeout_ms}")
                    saver.setup()
                    checkpoint_config = {"configurable": {"thread_id": checkpoint_thread}}
                    resume = saver.get_tuple(checkpoint_config) is not None
                    if resume:
                        self._emit(
                            run.id,
                            "run.resumed",
                            payload={"attempt": run.attempt},
                            on_event=on_event,
                        )
                    with use_config(dataflow_config):
                        execution = graph.execute(
                            context,
                            checkpointer=saver,
                            checkpoint_thread_id=checkpoint_thread,
                            resume=resume,
                            on_event=lambda raw: self._persist_graph_event(run.id, raw, on_event),
                        )
                    # Production graphs seal before deliberation. This
                    # idempotent application boundary also protects custom
                    # graph implementations from completing without durable
                    # evidence.
                    self._persist_evidence(
                        run.id,
                        execution.evidence,
                        on_event,
                    )
                    result = self._result(
                        run.id,
                        execution,
                        metrics,
                        instrument_name=instrument_name,
                        instrument_local_name=instrument_local_name,
                    )
                    benchmark = self._benchmark(
                        run.request.ticker,
                        dataflow_config,
                    )
                    revision_draft = None
                    if run.research_chain_requested or run.research_chain_id:
                        revision_draft = self.state_assembler(run.request, execution)
                    if run.research_chain_id and revision_draft is not None:
                        if run.baseline_revision_id is None:
                            raise ValueError("Research Chain update has no baseline")
                        baseline = self.repository.get_research_revision(run.baseline_revision_id)
                        if baseline.current_state.questions:
                            if self.repository.cancel_requested(run.id):
                                raise RunCancelled("cancelled before Question Disposition")
                            revision_draft = self.question_dispositioner(
                                baseline,
                                revision_draft,
                                quick_serializer_llm,
                            )
                            if self.repository.cancel_requested(run.id):
                                raise RunCancelled("cancelled after Question Disposition")
                            result = result.model_copy(update={"metrics": metrics.snapshot()})
                            disposition = revision_draft.delta.question_disposition
                            self._emit(
                                run.id,
                                "research.question_disposition_completed",
                                payload={
                                    "status": disposition.status if disposition else "limited",
                                    "limitation_reason": (
                                        disposition.limitation_reason.value
                                        if disposition is not None
                                        and disposition.limitation_reason is not None
                                        else None
                                    ),
                                    "repair_attempted": (
                                        disposition.repair_attempted if disposition else False
                                    ),
                                },
                                on_event=on_event,
                            )
                        revision_draft = self.revision_comparator(
                            baseline.id,
                            baseline,
                            revision_draft,
                        )
                    if update_audit is not None and revision_draft is not None:
                        bounded_snapshot = (
                            incremental_result.candidate
                            if incremental_result is not None
                            and incremental_result.candidate is not None
                            else incremental_result.evidence_snapshot
                            if incremental_result is not None
                            else None
                        )
                        if bounded_snapshot is not None:
                            revision_draft = close_revision_over_update_candidate(
                                revision_draft,
                                bounded_snapshot,
                            )
                        current_full_metrics = metrics.snapshot()
                        full_metrics = merge_run_metrics(
                            (
                                prior_update_audit.full_metrics
                                if prior_update_audit is not None
                                else RunMetrics()
                            ),
                            current_full_metrics,
                        )
                        comparison = derive_shadow_comparison(
                            (
                                incremental_result.candidate
                                if incremental_result is not None
                                else None
                            ),
                            revision_draft,
                        )
                        update_audit = update_audit.model_copy(
                            update={
                                "comparison": comparison,
                                "full_metrics": full_metrics,
                            }
                        )
                        self.repository.set_research_update_audit(run.id, update_audit)
                        revision_draft = ResearchRevisionDraft.model_validate(
                            revision_draft.model_copy(
                                update={"research_update_audit": update_audit}
                            ).model_dump(mode="python")
                        )
                        result = result.model_copy(
                            update={
                                "metrics": merge_run_metrics(
                                    (
                                        incremental_result.metrics
                                        if incremental_result is not None
                                        else RunMetrics()
                                    ),
                                    current_full_metrics,
                                )
                            }
                        )
                        self._emit(
                            run.id,
                            "research.shadow_compared",
                            payload={
                                "comparison": comparison,
                                "authoritative_outcome": (
                                    revision_draft.change_conclusion.value
                                    if revision_draft.change_conclusion is not None
                                    else None
                                ),
                                "metrics": {
                                    "bounded": update_audit.bounded_metrics.model_dump(mode="json"),
                                    "full": full_metrics.model_dump(mode="json"),
                                },
                            },
                            on_event=on_event,
                        )
                    aggregate_metrics = self.repository.complete(
                        run.id,
                        result,
                        evidence=execution.evidence,
                        benchmark=benchmark,
                        revision_draft=revision_draft,
                    )
                    result = result.model_copy(update={"metrics": aggregate_metrics})
                    saver.delete_thread(checkpoint_thread)
                self._emit(
                    run.id,
                    "run.succeeded",
                    payload={"metrics": result.metrics.model_dump(mode="json")},
                    on_event=on_event,
                )
                return result
            except RunCancelled:
                persist_partial_full_metrics()
                segment_metrics = _segment_metrics(incremental_result, update_audit, metrics)
                aggregate_metrics = self.repository.finish_cancel(
                    run.id,
                    metrics=segment_metrics,
                )
                self._clear_checkpoint(checkpoint_thread)
                self._emit(
                    run.id,
                    "run.cancelled",
                    payload={"metrics": aggregate_metrics.model_dump(mode="json")},
                    on_event=on_event,
                )
                return AnalysisResult(
                    run_id=run.id,
                    status=RunStatus.CANCELLED,
                    instrument=run.request.ticker,
                    instrument_name=instrument_name,
                    instrument_local_name=instrument_local_name,
                    reports={},
                    decision=None,
                    metrics=aggregate_metrics,
                    recoveries=self.repository.list_recoveries(run.id),
                    warnings=("Run cancelled at a graph node boundary.",),
                )
            except WorkerShutdown:
                persist_partial_full_metrics()
                segment_metrics = _segment_metrics(incremental_result, update_audit, metrics)
                released = self.repository.release_claim(
                    run.id,
                    worker_id,
                    metrics=segment_metrics,
                )
                self._emit(
                    run.id,
                    "run.interrupted",
                    payload={
                        "reason": "worker_shutdown",
                        "metrics": released.metrics.model_dump(mode="json"),
                    },
                    on_event=on_event,
                )
                raise
            except Exception as exc:
                persist_partial_full_metrics()
                segment_metrics = _segment_metrics(incremental_result, update_audit, metrics)
                try:
                    aggregate_metrics = self.repository.fail(
                        run.id,
                        exc,
                        metrics=segment_metrics,
                    )
                except Exception as persistence_exc:
                    logger.error(
                        "failed to persist terminal state for run %s (analysis=%s persistence=%s)",
                        run.id,
                        type(exc).__name__,
                        type(persistence_exc).__name__,
                    )
                    aggregate_metrics = segment_metrics
                try:
                    self._emit(
                        run.id,
                        "run.failed",
                        payload={
                            "error_code": type(exc).__name__,
                            "message": ("Analysis failed; inspect the server log."),
                            "metrics": aggregate_metrics.model_dump(mode="json"),
                        },
                        on_event=on_event,
                    )
                except Exception as event_exc:
                    logger.error(
                        "failed to persist failure event for run %s (analysis=%s event=%s)",
                        run.id,
                        type(exc).__name__,
                        type(event_exc).__name__,
                    )
                raise

    def cancel(self, run_id: str) -> RunView:
        view = self.repository.request_cancel(run_id)
        self.repository.append_event(
            run_id,
            ("run.cancelled" if view.status is RunStatus.CANCELLED else "run.cancel_requested"),
            payload={},
        )
        return view

    def retry(self, run_id: str) -> RunView:
        view = self.repository.retry(run_id)
        self.repository.append_event(
            run_id,
            "run.retry_queued",
            payload={"attempt": view.attempt},
        )
        return view

    def export(
        self,
        run_id: str,
        *,
        format: str = "markdown",
    ) -> tuple[str, str | bytes]:
        run_export = self.get_export(run_id)
        if format == "json":
            return (
                "application/json",
                run_export.model_dump_json(indent=2),
            )
        if format == "package":
            return (
                "application/zip",
                render_run_export_package(run_export),
            )
        if format != "markdown":
            raise ValueError("format must be 'markdown', 'json', or 'package'")
        return (
            "text/markdown; charset=utf-8",
            render_run_export_markdown(run_export),
        )

    def get_export(self, run_id: str) -> RunExport:
        run = self.repository.get_run(run_id)
        result = self.repository.get_result(run_id)
        return RunExport(
            run=run,
            result=result,
            evidence=result.evidence,
            artifacts=tuple(self.repository.list_artifacts(run_id)),
            attempts=self.repository.list_attempts(run_id),
        )

    def backup_database(self, destination: Path) -> Path:
        return self.repository.backup(destination)

    def get_revision_export(self, revision_id: str) -> RevisionExport:
        revision = self.repository.get_research_revision(revision_id)
        chain = self.get_research_chain(revision.chain_id)
        linked_reports: dict[str, str] = {}
        if revision.producing_run_id is not None:
            result = self.repository.get_result(revision.producing_run_id)
            linked_reports = {
                role: report.markdown
                for role, report in result.reports.items()
                if isinstance(report, AnalystReport)
            }
        return RevisionExport(
            chain=chain,
            revision=revision,
            linked_reports=linked_reports,
        )

    def export_revision(
        self,
        revision_id: str,
        *,
        format: str = "markdown",
    ) -> tuple[str, str | bytes]:
        export = self.get_revision_export(revision_id)
        if format == "json":
            return "application/json", export.model_dump_json(indent=2)
        if format == "package":
            return "application/zip", render_revision_export_package(export)
        if format != "markdown":
            raise ValueError("format must be 'markdown', 'json', or 'package'")
        return "text/markdown; charset=utf-8", render_revision_export_markdown(export)

    def _result(
        self,
        run_id: str,
        execution: GraphExecution,
        metrics: MetricsCallback,
        *,
        instrument_name: str | None,
        instrument_local_name: str | None,
    ) -> AnalysisResult:
        warnings = tuple(
            dict.fromkeys(
                (
                    *execution.warnings,
                    *(
                        warning
                        for report in execution.reports.values()
                        for warning in report.warnings
                    ),
                )
            )
        )
        return AnalysisResult(
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            instrument=execution.evidence.instrument,
            instrument_name=instrument_name,
            instrument_local_name=instrument_local_name,
            reports=execution.reports,
            decision=execution.decision,
            numeric_audit=execution.numeric_audit,
            evidence=execution.evidence,
            metrics=metrics.snapshot(),
            recoveries=self.repository.list_recoveries(run_id),
            warnings=warnings,
        )

    def _persist_graph_event(
        self,
        run_id: str,
        raw: dict[str, Any],
        on_event: EventHandler | None,
    ) -> None:
        self._emit(
            run_id,
            str(raw.get("event_type", "graph.event")),
            node=raw.get("node"),
            payload=dict(raw.get("payload") or {}),
            on_event=on_event,
        )

    def _persist_artifact(
        self,
        run_id: str,
        draft: ResearchArtifactDraft,
        on_event: EventHandler | None,
    ) -> None:
        _, event = self.repository.append_artifact(run_id, draft)
        if event is not None and on_event is not None:
            on_event(event)

    def _persist_evidence(
        self,
        run_id: str,
        evidence: EvidenceBundle,
        on_event: EventHandler | None,
    ) -> None:
        _, event = self.repository.seal_evidence(run_id, evidence)
        if event is not None and on_event is not None:
            on_event(event)

    def _emit(
        self,
        run_id: str,
        event_type: str,
        *,
        node: str | None = None,
        payload: dict[str, Any] | None = None,
        on_event: EventHandler | None = None,
    ) -> RunEvent:
        event = self.repository.append_event(
            run_id,
            event_type,
            node=node,
            payload=payload,
        )
        if on_event:
            try:
                on_event(event)
            except Exception:
                logger.exception("run event callback failed for %s", run_id)
        return event

    def _clear_checkpoint(self, checkpoint_thread: str) -> None:
        with SqliteSaver.from_conn_string(str(self.settings.database_path)) as saver:
            saver.conn.execute(f"PRAGMA busy_timeout={self.settings.busy_timeout_ms}")
            saver.setup()
            saver.delete_thread(checkpoint_thread)

    @contextmanager
    def _heartbeat(self, run_id: str, worker_id: str):
        stop = threading.Event()
        interval = max(5.0, min(30.0, self.settings.lease_seconds / 3))

        def beat() -> None:
            while not stop.wait(interval):
                if not self.repository.heartbeat(
                    run_id,
                    worker_id,
                    self.settings.lease_seconds,
                ):
                    return

        thread = threading.Thread(
            target=beat,
            name=f"lease-heartbeat-{run_id}",
            daemon=True,
        )
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1)

    @staticmethod
    def _benchmark(ticker: str, config: dict[str, Any]) -> str:
        explicit = config.get("benchmark_ticker")
        if explicit:
            return normalize_symbol(str(explicit))
        benchmark_map = config.get("benchmark_map", {})
        suffix = match_exchange_suffix(ticker, benchmark_map)
        if suffix:
            return benchmark_map[suffix]
        return benchmark_map.get("", "SPY")
