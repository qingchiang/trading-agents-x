"""Application service owning the complete analysis lifecycle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    resolve_instrument_identity,
)
from tradingagents.dataflows.config import use_config
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.interface import (
    get_vendor,
    parse_vendor_chain,
    route_to_vendor,
    validate_market_routing,
)
from tradingagents.dataflows.symbol_utils import (
    market_timezone,
    match_exchange_suffix,
    normalize_symbol,
)
from tradingagents.graph.research_graph import GraphExecution, ResearchGraph
from tradingagents.persistence import upgrade_database

from .anchor_readiness import (
    AnchorReadinessError,
    AnchorReadinessResult,
    validate_japanese_anchor_readiness,
)
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
    ResearchUpdateTransitionCoverage,
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
from .market_readiness import (
    MarketDataNotReadyError,
    MarketDataReadiness,
    validate_jquants_daily_bar_ready,
)
from .metrics import MetricsCallback, merge_run_metrics
from .question_disposition import run_full_question_disposition
from .repository import (
    EvidenceNotSealedError,
    InvalidResearchBaselineError,
    RunRepository,
    RunView,
)
from .research import (
    AnchorQualificationReason,
    IncrementalEscalationReason,
    IncrementalGateResult,
    ResearchChain,
    ResearchExecutionStrategy,
    ResearchRevision,
    ResearchRevisionDraft,
    RevisionExport,
    assemble_full_revision,
    assemble_full_update,
    bind_information_frontier,
    close_revision_over_update_candidate,
    derive_shadow_comparison,
    evaluate_next_update_policy,
    prepare_experimental_nmc_revision,
    render_revision_export_markdown,
    render_revision_export_package,
    transition_coverage_is_complete,
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
    readiness_metrics: RunMetrics | None = None,
):
    full_metrics = metrics.snapshot()
    bounded_metrics = (
        incremental_result.metrics
        if incremental_result is not None
        else update_audit.bounded_metrics
        if update_audit is not None
        else None
    )
    segments = tuple(
        item for item in (readiness_metrics, bounded_metrics, full_metrics) if item is not None
    )
    return merge_run_metrics(*segments)


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
        market_data_readiness_checker: Callable[
            [str, date], MarketDataReadiness | None
        ] = validate_jquants_daily_bar_ready,
        anchor_readiness_checker: Callable[..., AnchorReadinessResult] = (
            validate_japanese_anchor_readiness
        ),
        utc_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
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
        self.market_data_readiness_checker = market_data_readiness_checker
        self.anchor_readiness_checker = anchor_readiness_checker
        self.utc_clock = utc_clock

    def _target_information_frontier(self, request: AnalysisRequest) -> datetime:
        current = self.utc_clock()
        if current.utcoffset() is None:
            raise ValueError("Information Frontier clock requires a timezone")
        market_tz = market_timezone(request.ticker)
        market_now = current.astimezone(market_tz)
        if request.analysis_date > market_now.date():
            raise MarketDataNotReadyError(
                f"Research cutoff {request.analysis_date} is in the future in {market_tz}."
            )
        return (
            datetime.combine(request.analysis_date, time.max, tzinfo=market_tz)
            if request.analysis_date < market_now.date()
            else market_now
        )

    def _freeze_information_frontier(
        self,
        run: RunView,
        frontier: datetime | None = None,
    ) -> datetime:
        if run.information_frontier is not None:
            return run.information_frontier
        return self.repository.freeze_information_frontier(
            run.id,
            frontier or self._target_information_frontier(run.request),
        )

    @staticmethod
    def _uses_primary_jquants_market_route(
        request: AnalysisRequest,
        dataflow_config: dict[str, Any],
    ) -> bool:
        if not request.ticker.endswith(".T"):
            return False
        routes = dataflow_config.get("data_vendors_by_market", {})
        suffix = match_exchange_suffix(request.ticker, routes)
        raw_chain = get_vendor(
            "technical_indicators",
            "get_verified_market_snapshot",
            suffix,
            dataflow_config,
        )
        vendors = parse_vendor_chain(raw_chain)
        return bool(vendors and vendors[0] == "jquants")

    def validate_market_data_readiness(
        self,
        request: AnalysisRequest,
        *,
        run_settings: RunSettings | None = None,
        dataflow_config: dict[str, Any] | None = None,
    ) -> MarketDataReadiness | None:
        """Validate source-qualified Japanese market data without constructing an LLM."""
        resolved = run_settings or self.settings.resolve_run(request)
        config = dataflow_config or resolved.dataflow_config(self.settings)
        validate_market_routing(config)
        if not self._uses_primary_jquants_market_route(request, config):
            return None
        with use_config(config):
            try:
                return self.market_data_readiness_checker(
                    request.ticker,
                    request.analysis_date,
                )
            except NoMarketDataError as exc:
                raise MarketDataNotReadyError(
                    f"J-Quants daily bar is not ready for {request.analysis_date}."
                ) from exc

    def validate_anchor_readiness(
        self,
        request: AnalysisRequest,
        *,
        run_settings: RunSettings | None = None,
        dataflow_config: dict[str, Any] | None = None,
        information_frontier: datetime | None = None,
        anchor_frontier: datetime | None = None,
    ) -> AnchorReadinessResult | None:
        """Check Japanese minimum anchor capabilities without constructing an LLM."""
        if not request.ticker.endswith(".T"):
            return None
        resolved = run_settings or self.settings.resolve_run(request)
        config = dataflow_config or resolved.dataflow_config(self.settings)
        validate_market_routing(config)
        frontier = information_frontier or self._target_information_frontier(request)

        def collect_news(ticker, start, end, *, information_frontier):
            return route_to_vendor(
                "get_news",
                ticker,
                start,
                end,
                _provenance=True,
                information_frontier=information_frontier,
            )

        market_checker = (
            self.market_data_readiness_checker
            if self._uses_primary_jquants_market_route(request, config)
            else lambda _ticker, _cutoff: None
        )

        with use_config(config):
            return self.anchor_readiness_checker(
                request,
                information_frontier=frontier,
                market_checker=market_checker,
                news_collector=collect_news,
                anchor_frontier=anchor_frontier,
            )

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
                "forward_research_anchor": (
                    revision.coverage.anchor_qualification or chain.forward_research_anchor
                ),
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
                "Forward Research Anchor does not allow Incremental Execution: "
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
        information_frontier: datetime | None = None,
        on_event: EventHandler | None = None,
    ) -> tuple[RunView, AnalysisResult]:
        """Own one synchronous Research Chain update from enqueue through completion."""
        view = self.enqueue_chain_update(
            chain_id,
            baseline_revision_id,
            request,
            idempotency_key=idempotency_key,
        )
        if information_frontier is not None:
            self._freeze_information_frontier(view, information_frontier)
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
        readiness_metrics: RunMetrics | None = None
        anchor_readiness_result: AnchorReadinessResult | None = None
        full_analysis_executed = False
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
            transition_coverage_incomplete = (
                candidate is not None and not incremental_transition_is_complete(result)
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
                        coverage={
                            **candidate.coverage.model_dump(
                                mode="json",
                                exclude={"anchor_qualification"},
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
                        **bounded_coverage.model_dump(
                            mode="json",
                            exclude={"anchor_qualification"},
                        ),
                        "schema_version": "1",
                    }
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
                transition_coverage=(
                    ResearchUpdateTransitionCoverage.model_validate(
                        result.transition_coverage.model_dump(mode="json")
                    )
                    if result.transition_coverage is not None
                    else None
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
                baseline_information_frontier=baseline.information_frontier,
                escalation_reason=(
                    IncrementalEscalationReason.COVERAGE_INCOMPLETE.value
                    if transition_coverage_incomplete
                    else result.escalation_reason.value
                    if result.escalation_reason is not None
                    else None
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

        def incremental_transition_is_complete(result: IncrementalGateResult) -> bool:
            return (
                baseline.information_frontier is not None
                and information_frontier is not None
                and transition_coverage_is_complete(
                    baseline,
                    result.transition_coverage,
                    anchor_frontier=baseline.information_frontier,
                    update_frontier=information_frontier,
                )
            )

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

        def ensure_anchor_readiness_for_full() -> None:
            nonlocal anchor_readiness_result, full_analysis_executed
            nonlocal information_frontier, readiness_metrics
            if full_analysis_executed:
                return
            baseline_for_readiness = (
                self.repository.get_research_revision(run.baseline_revision_id)
                if run.baseline_revision_id is not None
                else None
            )
            if run.request.anchor_readiness == "required":
                readiness = self._prior_successful_anchor_readiness(run.id)
                reused_readiness = readiness is not None
                if readiness is None:
                    readiness = self.validate_anchor_readiness(
                        run.request,
                        run_settings=run_settings,
                        dataflow_config=dataflow_config,
                        information_frontier=run.information_frontier,
                        anchor_frontier=(
                            baseline_for_readiness.information_frontier
                            if baseline_for_readiness is not None
                            else None
                        ),
                    )
                if readiness is not None:
                    anchor_readiness_result = readiness
                    readiness_metrics = (
                        RunMetrics() if reused_readiness else readiness.metrics
                    )
                    if not readiness.ready:
                        self._emit(
                            run.id,
                            "research.anchor_readiness_failed",
                            payload=readiness.model_dump(mode="json"),
                            on_event=on_event,
                        )
                        raise AnchorReadinessError(readiness)
                    self._emit(
                        run.id,
                        (
                            "research.anchor_readiness_reused"
                            if reused_readiness
                            else "research.anchor_readiness_succeeded"
                        ),
                        payload=readiness.model_dump(mode="json"),
                        on_event=on_event,
                    )
                    information_frontier = self._freeze_information_frontier(
                        run,
                        readiness.information_frontier,
                    )
                else:
                    information_frontier = self._freeze_information_frontier(run)
            else:
                self._emit(
                    run.id,
                    "research.anchor_readiness_not_required",
                    payload={
                        "anchor_readiness": "allow_non_anchor",
                        "next_update_policy_if_unqualified": "full_required",
                    },
                    on_event=on_event,
                )
                information_frontier = self._freeze_information_frontier(run)
            full_analysis_executed = True

        with self._heartbeat(run.id, worker_id):
            try:
                with use_config(dataflow_config):
                    planned_full_analysis = run.research_chain_requested or (
                        run.research_chain_id is not None
                        and run.research_execution_strategy == "full"
                    ) or (
                        run.research_chain_id is not None
                        and run_settings.research_update_mode == "shadow"
                    )
                    if planned_full_analysis:
                        ensure_anchor_readiness_for_full()
                    elif run.research_chain_id:
                        readiness = self.validate_market_data_readiness(
                            run.request,
                            run_settings=run_settings,
                            dataflow_config=dataflow_config,
                        )
                        if readiness is not None:
                            self._emit(
                                run.id,
                                "research.market_data_ready",
                                payload={
                                    "source": "J-Quants daily OHLCV",
                                    "requested_cutoff": (readiness.requested_cutoff.isoformat()),
                                    "market_effective_date": (
                                        readiness.market_effective_date.isoformat()
                                    ),
                                    "observed_bar_date": (readiness.observed_bar_date.isoformat()),
                                },
                                on_event=on_event,
                            )
                        information_frontier = self._freeze_information_frontier(run)
                    else:
                        information_frontier = None
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
                                information_frontier=information_frontier,
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
                            and not incremental_transition_is_complete(incremental_result)
                        ):
                            incremental_result = incremental_result.model_copy(
                                update={
                                    "candidate": None,
                                    "escalation_reason": (
                                        IncrementalEscalationReason.COVERAGE_INCOMPLETE
                                    ),
                                }
                            )
                            candidate = None
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
                                "transition_coverage": (
                                    update_audit.transition_coverage.model_dump(mode="json")
                                    if update_audit.transition_coverage is not None
                                    else None
                                ),
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
                            revision_draft = bind_information_frontier(
                                revision_draft,
                                information_frontier,
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
                        ensure_anchor_readiness_for_full()
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
                    memory = MemoryContext(
                        instrument=run.request.ticker,
                        market=self.repository.market_bucket(run.request.ticker),
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
                    information_frontier=information_frontier,
                    anchor_readiness=anchor_readiness_result,
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
                    sealed_evidence_reader=lambda: self._read_sealed_evidence(
                        run.id
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
                    if readiness_metrics is not None:
                        result = result.model_copy(
                            update={
                                "metrics": merge_run_metrics(
                                    readiness_metrics,
                                    result.metrics,
                                )
                            }
                        )
                    benchmark = self._benchmark(
                        run.request.ticker,
                        dataflow_config,
                    )
                    revision_draft = None
                    if run.research_chain_requested or run.research_chain_id:
                        revision_draft = self.state_assembler(run.request, execution)
                        revision_draft = bind_information_frontier(
                            revision_draft,
                            information_frontier,
                        )
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
                            result = result.model_copy(
                                update={
                                    "metrics": merge_run_metrics(
                                        *(
                                            item
                                            for item in (
                                                readiness_metrics,
                                                metrics.snapshot(),
                                            )
                                            if item is not None
                                        )
                                    )
                                }
                            )
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
                                    *(
                                        item
                                        for item in (
                                            readiness_metrics,
                                            (
                                                incremental_result.metrics
                                                if incremental_result is not None
                                                else RunMetrics()
                                            ),
                                            current_full_metrics,
                                        )
                                        if item is not None
                                    )
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
                    if revision_draft is not None:
                        revision_draft = bind_information_frontier(
                            revision_draft,
                            information_frontier,
                        )
                        if (
                            full_analysis_executed
                            and run.request.anchor_readiness == "allow_non_anchor"
                            and revision_draft.coverage.anchor_qualification is not None
                        ):
                            qualification = revision_draft.coverage.anchor_qualification
                            revision_draft = revision_draft.model_copy(
                                update={
                                    "coverage": revision_draft.coverage.model_copy(
                                        update={
                                            "anchor_qualification": qualification.model_copy(
                                                update={
                                                    "is_forward_research_anchor": False,
                                                    "reasons": (
                                                        AnchorQualificationReason.ANCHOR_READINESS_NOT_REQUIRED,
                                                    ),
                                                }
                                            )
                                        }
                                    )
                                }
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
                segment_metrics = _segment_metrics(
                    incremental_result,
                    update_audit,
                    metrics,
                    readiness_metrics,
                )
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
                segment_metrics = _segment_metrics(
                    incremental_result,
                    update_audit,
                    metrics,
                    readiness_metrics,
                )
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
                segment_metrics = _segment_metrics(
                    incremental_result,
                    update_audit,
                    metrics,
                    readiness_metrics,
                )
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

    def _read_sealed_evidence(self, run_id: str) -> EvidenceBundle | None:
        try:
            return self.repository.get_evidence(run_id)
        except EvidenceNotSealedError:
            return None

    def _prior_successful_anchor_readiness(
        self,
        run_id: str,
    ) -> AnchorReadinessResult | None:
        return next(
            (
                AnchorReadinessResult.model_validate(event.payload)
                for event in self.repository.list_events(run_id, limit=2000)
                if event.event_type == "research.anchor_readiness_succeeded"
            ),
            None,
        )

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
