"""Application service owning the complete analysis lifecycle."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, time
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    resolve_instrument_identity,
)
from tradingagents.dataflows.config import use_config
from tradingagents.dataflows.interface import (
    resolve_instrument_eligibility,
    validate_market_routing,
)
from tradingagents.dataflows.symbol_utils import market_timezone
from tradingagents.graph.research_graph import GraphExecution, ResearchGraph
from tradingagents.persistence import upgrade_database
from tradingagents.version import __version__

from .contracts import (
    CURRENT_RESEARCH_SCHEMA_VERSION,
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    FullResearchRequiredReason,
    IncrementalNodeProducts,
    IncrementalSynthesis,
    IncrementalSynthesisInput,
    NodeMetrics,
    PerformanceObservation,
    ReassessmentDisposition,
    ResearchArtifactDraft,
    ResearchReassessment,
    ResearchReassessmentEntry,
    RunEvent,
    RunExport,
    RunMetrics,
    RunStatus,
)
from .eligibility import validate_instrument_eligibility
from .errors import (
    IncrementalCollectionCommitUnavailableError,
    InstrumentEligibilityUnavailableError,
    NoInformationAdvancementError,
    UnsupportedInstrumentError,
)
from .exporting import (
    render_run_export_markdown,
    render_run_export_package,
)
from .incremental_collection import (
    IncrementalCollector,
    assess_incremental_collection,
    build_incremental_collection_plan,
    default_incremental_collector,
    incremental_market_identity,
)
from .instrument_names import resolve_local_instrument_name
from .llms import RunLLMs, create_run_llms
from .metrics import MetricsCallback, merge_run_metrics
from .repository import RunRepository, RunView
from .runtime import RunCancelled, RunContext, WorkerShutdown
from .settings import AppSettings, RunSettings

logger = logging.getLogger(__name__)

EventHandler = Callable[[RunEvent], None]
EligibilityResolver = Callable[[str], Any]
IncrementalSynthesizer = Callable[[IncrementalSynthesisInput], IncrementalSynthesis]


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


def _baseline_component_ids(decision) -> tuple[str, ...]:
    component_ids = ["executive_summary", "thesis"]
    for field in ("catalysts", "risks", "invalidation_conditions"):
        component_ids.extend(f"{field}.{index}" for index, _ in enumerate(getattr(decision, field)))
    for scenario in decision.scenarios:
        component_ids.append(f"scenarios.{scenario.kind.value}.outcome")
        component_ids.extend(
            f"scenarios.{scenario.kind.value}.core_assumptions.{index}"
            for index, _ in enumerate(scenario.core_assumptions)
        )
    component_ids.extend(
        f"risk_review_adjustments.{index}.explanation"
        for index, _ in enumerate(decision.risk_review_adjustments)
    )
    return tuple(component_ids)


def default_incremental_synthesizer(
    synthesis_input: IncrementalSynthesisInput,
) -> IncrementalSynthesis:
    """Offline contract default; production wiring can replace this semantic seam."""
    return IncrementalSynthesis(
        reassessment=ResearchReassessment(
            entries=tuple(
                ResearchReassessmentEntry(
                    component_id=component_id,
                    disposition=ReassessmentDisposition.REAFFIRMED,
                    reason="A complete-empty collection scan found no new matching record.",
                )
                for component_id in _baseline_component_ids(synthesis_input.full_baseline_decision)
            )
        ),
        decision=synthesis_input.full_baseline_decision,
    )


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
        eligibility_resolver: EligibilityResolver = resolve_instrument_eligibility,
        local_name_resolver: Callable[[str, str, dict[str, Any]], str | None] = (
            resolve_local_instrument_name
        ),
        incremental_collector: IncrementalCollector = default_incremental_collector,
        incremental_synthesizer: IncrementalSynthesizer = default_incremental_synthesizer,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.settings = settings
        if repository is None:
            upgrade_database(settings)
        self.repository = repository or RunRepository(settings)
        self.llm_factory = llm_factory
        self.graph_factory = graph_factory
        self.identity_resolver = identity_resolver
        if eligibility_resolver is None:
            raise TypeError("eligibility_resolver is required")
        self.eligibility_resolver = eligibility_resolver
        self.local_name_resolver = local_name_resolver
        self.incremental_collector = incremental_collector
        self.incremental_synthesizer = incremental_synthesizer
        self.now = now

    def enqueue(
        self,
        request: AnalysisRequest,
        *,
        idempotency_key: str | None = None,
        source_run_id: str | None = None,
    ) -> RunView:
        if not isinstance(request, AnalysisRequest):
            raise TypeError("new Runs require an AnalysisRequest creation contract")
        # Re-run the creation validators at the lifecycle seam.  A caller can
        # otherwise bypass Pydantic validation with ``model_construct`` and
        # hand the repository an invalid request that would still be durable.
        request = AnalysisRequest.model_validate(request.model_dump(mode="json", warnings=False))
        run_settings = self.settings.resolve_run(request)
        self._validate_instrument_eligibility(request)
        if source_run_id is not None:
            # A retained Run may carry a legacy request that is intentionally
            # readable but no longer admitted as a source for new research.
            source_request = self._creation_request_from_history(
                self.repository.get_run(source_run_id).request
            )
            self._validate_instrument_eligibility(source_request)
        request = self.settings.materialize_request(
            request,
            run_settings=run_settings,
        )
        information_cutoff_at = self._information_cutoff_at(request)
        method_snapshot = self._method_snapshot(run_settings, request)
        if request.research_kind == "incremental":
            assert request.full_baseline_run_id is not None
            self.repository.validate_incremental_baseline(
                request.full_baseline_run_id,
                request,
            )
        view, created = self.repository.create_run(
            request,
            run_settings.snapshot(),
            idempotency_key=idempotency_key,
            source_run_id=source_run_id,
            research_schema_version=CURRENT_RESEARCH_SCHEMA_VERSION,
            information_cutoff_at=information_cutoff_at,
            method_snapshot=method_snapshot,
            research_kind=request.research_kind,
            full_baseline_run_id=request.full_baseline_run_id,
            incremental_input_fingerprint=(
                self._incremental_input_fingerprint(
                    request,
                    method_snapshot,
                    source_run_id=source_run_id,
                )
                if request.research_kind == "incremental"
                else None
            ),
        )
        if created:
            self.repository.append_event(
                view.id,
                "run.queued",
                payload={
                    "profile": request.profile.value,
                    "ticker": request.ticker,
                    "source_run_id": source_run_id,
                },
            )
        return view

    @staticmethod
    def _incremental_input_fingerprint(
        request: AnalysisRequest,
        method_snapshot: dict[str, Any],
        *,
        source_run_id: str | None,
    ) -> str:
        """Hash the immutable inputs that define one active Cycle/cutoff slot."""
        payload = {
            "request": request.model_dump(mode="json"),
            "source_run_id": source_run_id,
            "method_configuration_fingerprint": method_snapshot["configuration_fingerprint"],
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode()).hexdigest()

    def _information_cutoff_at(self, request: AnalysisRequest) -> datetime:
        """Freeze the one PIT boundary before the Run becomes durable."""
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        zone = market_timezone(request.ticker)
        market_now = now.astimezone(zone)
        if request.analysis_date > market_now.date():
            raise ValueError("future analysis cutoff is not allowed")
        if request.analysis_date == market_now.date():
            return now.astimezone(UTC)
        return datetime.combine(
            request.analysis_date,
            time.max,
            tzinfo=zone,
        ).astimezone(UTC)

    @staticmethod
    def _method_snapshot(
        run_settings: RunSettings,
        request: AnalysisRequest,
    ) -> dict[str, Any]:
        """Persist audit-relevant, redacted method choices without replay claims."""
        snapshot = run_settings.snapshot()
        data_config = snapshot["data_config"]
        method_snapshot = {
            "schema_version": "1",
            "research_schema_version": CURRENT_RESEARCH_SCHEMA_VERSION,
            "application_version": __version__,
            "prompt_versions": {
                "analyst": "v6-sealed-context",
                "research_case": "v6-readable",
                "debate_agenda": "v9-thinking-json",
                "rebuttal": "v5-compact",
                "research_judge": "v6-readable",
                "risk_review": "v6-readable",
                "final_committee_brief": "v3-input-evidence-binding",
                "final_committee": "v14-dimensionless-display-scale",
            },
            "llm_provider": snapshot["llm_provider"],
            "quick_model": snapshot["quick_model"],
            "deep_model": snapshot["deep_model"],
            "backend_url": snapshot["backend_url"],
            "quick_reasoning_effort": snapshot["quick_reasoning_effort"],
            "deep_reasoning_effort": snapshot["deep_reasoning_effort"],
            "temperature": snapshot["temperature"],
            "llm_max_retries": snapshot["llm_max_retries"],
            "output_language": snapshot["output_language"],
            "enabled_roles": list(request.analysts),
            "market_identity": incremental_market_identity(request.ticker),
            "data_routes": {
                "data_vendors": data_config.get("data_vendors", {}),
                "tool_vendors": data_config.get("tool_vendors", {}),
                "data_vendors_by_market": data_config.get("data_vendors_by_market", {}),
            },
            "coverage_policy": {
                "version": "1",
                "required_domains": ["fundamentals", "market", "news"],
                "advisory_domains": ["social"],
            },
            "thresholds": {
                key: data_config.get(key)
                for key in (
                    "news_article_limit",
                    "sentiment_filing_limit",
                    "ticker_news_lookback_days",
                    "social_lookback_days",
                    "global_news_article_limit",
                    "global_news_lookback_days",
                )
            },
        }
        canonical = json.dumps(
            method_snapshot,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            **method_snapshot,
            "configuration_fingerprint": sha256(canonical.encode()).hexdigest(),
        }

    def _validate_instrument_eligibility(
        self,
        request: AnalysisRequest,
    ) -> None:
        """Fail closed unless one exact resolver result confirms an equity."""
        try:
            dataflow_config = self.settings.default_run_settings.dataflow_config(self.settings)
            with use_config(dataflow_config, merge=False):
                result = self.eligibility_resolver(request.ticker)
            validate_instrument_eligibility(request.ticker, result)
        except (
            InstrumentEligibilityUnavailableError,
            UnsupportedInstrumentError,
        ):
            raise
        except Exception as exc:
            raise InstrumentEligibilityUnavailableError(
                request.ticker,
                f"resolver failed with {type(exc).__name__}",
            ) from exc

    @staticmethod
    def _creation_request_from_history(snapshot: Any) -> AnalysisRequest:
        """Convert a retained request through the current creation boundary."""
        try:
            return snapshot.to_analysis_request()
        except ValidationError as exc:
            raise UnsupportedInstrumentError(
                snapshot.ticker,
                snapshot.asset_type or "legacy request",
            ) from exc

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
        checkpoint_thread = self.repository.checkpoint_thread(run.id)
        self._emit(
            run.id,
            "run.started",
            payload={"attempt": run.attempt},
            on_event=on_event,
        )
        metrics = MetricsCallback()
        instrument_name = run.instrument_name
        instrument_local_name = run.instrument_local_name

        with self._heartbeat(run.id, worker_id):
            try:
                if run.research_schema_version is None:
                    raise ValueError(
                        "legacy runs cannot cross the Research Timeline execution boundary"
                    )
                # Run views expose a tolerant retained snapshot.  Execution
                # must cross the current creation contract explicitly so a
                # future admission change also gates already-queued legacy
                # requests.  Keep this inside the lifecycle boundary: a
                # retained request that no longer converts must become a
                # terminal failed Run rather than strand a claimed attempt.
                request = self._creation_request_from_history(run.request)
                run_settings = RunSettings.model_validate(run.config_snapshot)
                dataflow_config = run_settings.dataflow_config(self.settings)
                self._validate_instrument_eligibility(request)
                validate_market_routing(dataflow_config)
                if run.research_kind == "incremental":
                    assert request.full_baseline_run_id is not None
                    self.repository.validate_incremental_baseline(
                        request.full_baseline_run_id,
                        request,
                    )
                    baseline = self.repository.get_run(request.full_baseline_run_id)
                    collection = self._collect_incremental_preflight(
                        baseline_information_cutoff_at=baseline.information_cutoff_at,
                        target_information_cutoff_at=run.information_cutoff_at,
                        method_snapshot=run.method_snapshot,
                    )
                    self._emit(
                        run.id,
                        "incremental.collection_completed",
                        payload=collection.model_dump(mode="json"),
                        on_event=on_event,
                    )
                    if not collection.information_advancement.advanced:
                        self._emit(
                            run.id,
                            "incremental.no_advancement",
                            payload={
                                "reason": "no_admissible_information_advancement",
                                "coverage_policy_version": collection.research_coverage.policy_version,
                                "diagnostics": [
                                    item.model_dump(mode="json") for item in collection.diagnostics
                                ],
                            },
                            on_event=on_event,
                        )
                        raise NoInformationAdvancementError(
                            "Incremental collection found no admissible information advancement."
                        )
                    has_complete_empty_scan = any(
                        entry.outcome.value == "complete_empty"
                        for entry in collection.collection_manifest.entries
                    )
                    if not has_complete_empty_scan or any(
                        entry.evidence_refs for entry in collection.collection_manifest.entries
                    ):
                        raise IncrementalCollectionCommitUnavailableError(
                            "Evidence-bearing Incremental Nodes are not available before Ticket 07."
                        )
                    baseline_result = self.repository.get_result(baseline.id)
                    if baseline_result.decision is None:
                        raise ValueError(
                            "Incremental synthesis requires a complete Full Baseline Decision"
                        )
                    baseline_evidence = self.repository.get_evidence(baseline.id)
                    performance = PerformanceObservation(
                        status="not_yet_observable",
                        reason="Performance calculation is not yet observable in the complete-empty tracer.",
                    )
                    synthesis_input = IncrementalSynthesisInput(
                        full_baseline_run_id=baseline.id,
                        full_baseline_decision=baseline_result.decision,
                        permitted_baseline_evidence_refs=tuple(
                            item.ref for item in baseline_evidence.items
                        ),
                        collection_manifest=collection.collection_manifest,
                        research_coverage=collection.research_coverage,
                        information_advancement=collection.information_advancement,
                        performance=performance,
                        outcome_review_status="omitted",
                        method_snapshot=run.method_snapshot,
                    )
                    self._emit(
                        run.id,
                        "incremental.synthesis_started",
                        payload={
                            "full_baseline_run_id": baseline.id,
                            "outcome_review_status": "omitted",
                        },
                        on_event=on_event,
                    )
                    synthesis = self.incremental_synthesizer(synthesis_input)
                    expected_components = set(_baseline_component_ids(baseline_result.decision))
                    if {
                        entry.component_id for entry in synthesis.reassessment.entries
                    } != expected_components:
                        raise ValueError(
                            "Incremental synthesis must reassess every Full Baseline Decision Component"
                        )
                    if not set(synthesis.decision.evidence_refs).issubset(
                        set(synthesis_input.permitted_baseline_evidence_refs)
                    ):
                        raise ValueError(
                            "Complete-empty Incremental decisions may reference only Full Baseline Evidence"
                        )
                    deterministic_reasons = tuple(
                        FullResearchRequiredReason(
                            code=f"required_coverage.{domain.domain}",
                            message=f"Required {domain.domain} coverage is {domain.status.value}.",
                            origin="deterministic",
                        )
                        for domain in collection.research_coverage.domains
                        if domain.requirement.value == "required"
                        and domain.status.value in {"limited", "missing"}
                    )
                    products = IncrementalNodeProducts(
                        collection_manifest=collection.collection_manifest,
                        research_coverage=collection.research_coverage,
                        information_advancement=collection.information_advancement,
                        performance=performance,
                        outcome_review_status="omitted",
                        reassessment=synthesis.reassessment,
                        full_research_required_reasons=(
                            *synthesis.full_research_required_reasons,
                            *deterministic_reasons,
                        ),
                    )
                    segment_metrics = merge_run_metrics(
                        metrics.snapshot(),
                        RunMetrics(
                            llm_calls=1,
                            node_metrics={"incremental.synthesis": NodeMetrics(llm_calls=1)},
                        ),
                    )
                    result = AnalysisResult(
                        run_id=run.id,
                        status=RunStatus.SUCCEEDED,
                        instrument=request.ticker,
                        reports={},
                        decision=synthesis.decision,
                        evidence=EvidenceBundle(
                            instrument=request.ticker,
                            analysis_date=request.analysis_date,
                            items=(),
                        ),
                        metrics=segment_metrics,
                    )
                    aggregate_metrics = self.repository.complete_incremental(
                        run.id,
                        result,
                        evidence=result.evidence,
                        products=products,
                    )
                    result = result.model_copy(update={"metrics": aggregate_metrics})
                    self._emit(
                        run.id,
                        "incremental.synthesis_completed",
                        payload={"metrics": result.metrics.model_dump(mode="json")},
                        on_event=on_event,
                    )
                    self._emit(
                        run.id,
                        "run.succeeded",
                        payload={"metrics": result.metrics.model_dump(mode="json")},
                        on_event=on_event,
                    )
                    return result
                with use_config(dataflow_config):
                    try:
                        identity = self.identity_resolver(
                            request.ticker,
                            request.analysis_date.isoformat(),
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
                                request.ticker,
                                request.analysis_date.isoformat(),
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
                        request.ticker,
                        identity=identity,
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
                    profile=request.profile,
                    selected_analysts=request.analysts,
                    metrics=metrics,
                )
                context = RunContext(
                    run_id=run.id,
                    request=request,
                    settings=run_settings,
                    dataflow_config=dataflow_config,
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
                    aggregate_metrics = self.repository.complete(
                        run.id,
                        result,
                        evidence=execution.evidence,
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
                aggregate_metrics = self.repository.finish_cancel(
                    run.id,
                    metrics=metrics.snapshot(),
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
                    instrument=request.ticker,
                    instrument_name=instrument_name,
                    instrument_local_name=instrument_local_name,
                    reports={},
                    decision=None,
                    metrics=aggregate_metrics,
                    recoveries=self.repository.list_recoveries(run.id),
                    warnings=("Run cancelled at a graph node boundary.",),
                )
            except WorkerShutdown:
                released = self.repository.release_claim(
                    run.id,
                    worker_id,
                    metrics=metrics.snapshot(),
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
                segment_metrics = metrics.snapshot()
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

    def _collect_incremental_preflight(
        self,
        *,
        baseline_information_cutoff_at: datetime | None,
        target_information_cutoff_at: datetime | None,
        method_snapshot: dict[str, Any],
    ):
        """Build and assess the deterministic collection gate before semantic work."""
        if baseline_information_cutoff_at is None or target_information_cutoff_at is None:
            raise ValueError("Incremental collection requires frozen information cutoffs")
        plan = build_incremental_collection_plan(
            market_identity=method_snapshot["market_identity"],
            data_routes=method_snapshot["data_routes"],
            coverage_policy=method_snapshot["coverage_policy"],
            window_start=baseline_information_cutoff_at,
            window_end=target_information_cutoff_at,
        )
        return assess_incremental_collection(plan, self.incremental_collector(plan))

    def cancel(self, run_id: str) -> RunView:
        view = self.repository.request_cancel(run_id)
        self.repository.append_event(
            run_id,
            ("run.cancelled" if view.status is RunStatus.CANCELLED else "run.cancel_requested"),
            payload={},
        )
        return view

    def retry(self, run_id: str) -> RunView:
        # Validate the retained request through the current creation contract
        # before mutating the retry lifecycle.  This keeps retry from becoming
        # an alternate execution path around current admission rules.
        retained = self.repository.require_retryable(run_id)
        request = self._creation_request_from_history(retained.request)
        self._validate_instrument_eligibility(request)
        if request.research_kind == "incremental":
            assert request.full_baseline_run_id is not None
            self.repository.validate_incremental_baseline(
                request.full_baseline_run_id,
                request,
            )
        view = self.repository.retry(run_id)
        if view.id != run_id:
            return view
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
