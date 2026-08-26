"""Application service owning the complete analysis lifecycle."""

from __future__ import annotations

import json
import logging
import math
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
from tradingagents.graph.structured_output import StructuredOutputRunner
from tradingagents.persistence import upgrade_database
from tradingagents.version import __version__

from .contracts import (
    CURRENT_RESEARCH_SCHEMA_VERSION,
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    FullResearchRequiredReason,
    IncrementalCollectionPreflight,
    IncrementalNodeProducts,
    IncrementalSynthesis,
    IncrementalSynthesisInput,
    PerformanceComponentStatus,
    PerformanceObservation,
    ReassessmentDisposition,
    ResearchArtifactDraft,
    ResearchNodeComparison,
    ResearchNodeComparisonSelection,
    ResearchReassessment,
    ResearchReassessmentEntry,
    RunEvent,
    RunExport,
    RunStatus,
    report_language_prompt_label,
)
from .eligibility import validate_instrument_eligibility
from .errors import (
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
    assess_information_advancement,
    build_incremental_collection_request,
    calculate_benchmark_performance,
    calculate_stock_performance,
    default_incremental_collector,
    derive_research_availability,
    incremental_market_identity,
    normalize_incremental_collection,
)
from .instrument_names import resolve_local_instrument_name
from .llms import RunLLMs, create_run_llms
from .metrics import MetricsCallback
from .repository import EvidenceConflictError, RunRepository, RunView
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
    """Test-only deterministic seam; production always supplies a model-backed synthesizer."""
    return IncrementalSynthesis(
        reassessment=ResearchReassessment(
            entries=tuple(
                ResearchReassessmentEntry(
                    component_id=component_id,
                    disposition=ReassessmentDisposition.REAFFIRMED,
                    reason="The bounded update does not change this baseline component.",
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
        incremental_synthesizer: IncrementalSynthesizer | None = None,
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

    def compare_research_nodes(
        self,
        instrument: str,
        selections: tuple[ResearchNodeComparisonSelection, ...],
    ) -> ResearchNodeComparison:
        """Compare retained Node products without starting research or writing state."""
        return self.repository.compare_research_nodes(instrument, selections)

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
        request_dataflow_config = run_settings.dataflow_config(self.settings)
        self._validate_instrument_eligibility(
            request,
            dataflow_config=request_dataflow_config,
        )
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
            "data_availability_policy": {
                "version": "1",
                "near_live_max_age_days": 5,
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
        *,
        dataflow_config: dict[str, Any] | None = None,
    ) -> None:
        """Fail closed unless one exact resolver result confirms an equity."""
        try:
            effective_config = dataflow_config or (
                self.settings.default_run_settings.dataflow_config(self.settings)
            )
            with use_config(effective_config, merge=False):
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
                self._validate_instrument_eligibility(
                    request,
                    dataflow_config=dataflow_config,
                )
                validate_market_routing(dataflow_config)
                if run.research_kind == "incremental":
                    assert request.full_baseline_run_id is not None
                    self.repository.validate_incremental_baseline(
                        request.full_baseline_run_id,
                        request,
                    )
                    baseline = self.repository.get_run(request.full_baseline_run_id)
                    baseline_evidence = self.repository.get_evidence(baseline.id)
                    with use_config(dataflow_config, merge=False):
                        collection, evidence_items, performance, sealed_at = (
                            self._collect_incremental_preflight(
                                instrument=request.ticker,
                                baseline_analysis_cutoff=baseline.request.analysis_date,
                                analysis_cutoff=request.analysis_date,
                                baseline_evidence=baseline_evidence,
                                baseline_information_cutoff_at=baseline.information_cutoff_at,
                                target_information_cutoff_at=run.information_cutoff_at,
                                method_snapshot=run.method_snapshot,
                            )
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
                                "availability_policy_version": (
                                    collection.research_availability.version
                                ),
                                "diagnostics": [
                                    item.model_dump(mode="json") for item in collection.diagnostics
                                ],
                            },
                            on_event=on_event,
                        )
                        raise NoInformationAdvancementError(
                            "Incremental collection found no admissible information advancement."
                        )
                    # Collection may take long enough for another connection to
                    # Trash or permanently purge the Full Baseline. Revalidate
                    # before reading any baseline products so both mutations
                    # fail as one typed lifecycle boundary.
                    self.repository.validate_incremental_baseline(
                        request.full_baseline_run_id,
                        request,
                    )
                    baseline_result = self.repository.get_result(baseline.id)
                    if baseline_result.decision is None:
                        raise ValueError(
                            "Incremental synthesis requires a complete Full Baseline Decision"
                        )
                    incremental_evidence = EvidenceBundle(
                        instrument=request.ticker,
                        analysis_date=request.analysis_date,
                        items=evidence_items,
                        sealed_at=sealed_at,
                    )
                    self._validate_incremental_bundle_ownership(
                        incremental_evidence,
                        baseline_evidence,
                    )
                    synthesis_input = IncrementalSynthesisInput(
                        full_baseline_run_id=baseline.id,
                        full_baseline_decision=baseline_result.decision,
                        permitted_baseline_evidence_refs=tuple(
                            item.ref for item in baseline_evidence.items
                        ),
                        incremental_evidence=incremental_evidence,
                        collection_summary=collection.collection_summary,
                        research_availability=collection.research_availability,
                        information_advancement=collection.information_advancement,
                        performance=performance,
                        method_snapshot=run.method_snapshot,
                    )
                    self._emit(
                        run.id,
                        "incremental.synthesis_started",
                        payload={
                            "full_baseline_run_id": baseline.id,
                        },
                        on_event=on_event,
                    )
                    if self.incremental_synthesizer is not None:
                        synthesis = self.incremental_synthesizer(synthesis_input)
                    else:
                        synthesis = self._run_incremental_synthesis(
                            synthesis_input,
                            run_settings=run_settings,
                            metrics=metrics,
                            run_id=run.id,
                            on_event=on_event,
                        )
                    expected_components = set(_baseline_component_ids(baseline_result.decision))
                    if {
                        entry.component_id for entry in synthesis.reassessment.entries
                    } != expected_components:
                        raise ValueError(
                            "Incremental synthesis must reassess every Full Baseline Decision Component"
                        )
                    allowed_evidence_refs = set(synthesis_input.permitted_baseline_evidence_refs)
                    allowed_evidence_refs.update(item.ref for item in incremental_evidence.items)
                    if not set(synthesis.decision.evidence_refs).issubset(allowed_evidence_refs):
                        raise ValueError(
                            "Incremental decisions may reference only the Full Baseline or current Evidence"
                        )
                    self._validate_reassessment_closure(synthesis, synthesis_input)
                    products = IncrementalNodeProducts(
                        collection_summary=collection.collection_summary,
                        research_availability=collection.research_availability,
                        information_advancement=collection.information_advancement,
                        performance=performance,
                        reassessment=synthesis.reassessment,
                        full_research_required_reasons=(synthesis.full_research_required_reasons),
                    )
                    self._validate_full_research_required_reason_closure(
                        products.full_research_required_reasons,
                        synthesis_input,
                    )
                    segment_metrics = metrics.snapshot()
                    result = AnalysisResult(
                        run_id=run.id,
                        status=RunStatus.SUCCEEDED,
                        instrument=request.ticker,
                        reports={},
                        decision=synthesis.decision,
                        evidence=incremental_evidence,
                        metrics=segment_metrics,
                    )
                    self._emit(
                        run.id,
                        "incremental.synthesis_completed",
                        payload={"metrics": result.metrics.model_dump(mode="json")},
                        on_event=on_event,
                    )
                    last_event = self.repository.list_events(run.id)[-1]
                    self._validate_instrument_eligibility(
                        request,
                        dataflow_config=dataflow_config,
                    )
                    aggregate_metrics = self.repository.complete_incremental(
                        run.id,
                        result,
                        evidence=result.evidence,
                        products=products,
                    )
                    result = result.model_copy(update={"metrics": aggregate_metrics})
                    if on_event is not None:
                        for event in self.repository.list_events(
                            run.id,
                            after_sequence=last_event.sequence,
                        ):
                            try:
                                on_event(event)
                            except Exception:
                                logger.exception("run event callback failed for %s", run.id)
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
        instrument: str,
        baseline_analysis_cutoff,
        analysis_cutoff,
        baseline_evidence: EvidenceBundle,
        baseline_information_cutoff_at: datetime | None,
        target_information_cutoff_at: datetime | None,
        method_snapshot: dict[str, Any],
    ):
        """Build and assess the deterministic collection gate before semantic work."""
        if baseline_information_cutoff_at is None or target_information_cutoff_at is None:
            raise ValueError("Incremental collection requires frozen information cutoffs")
        request = build_incremental_collection_request(
            instrument=instrument,
            baseline_analysis_cutoff=baseline_analysis_cutoff,
            analysis_cutoff=analysis_cutoff,
            market_identity=method_snapshot["market_identity"],
            data_routes=method_snapshot["data_routes"],
            data_availability_policy=method_snapshot["data_availability_policy"],
            enabled_domains=tuple(method_snapshot["enabled_roles"]),
            window_start=baseline_information_cutoff_at,
            window_end=target_information_cutoff_at,
        )
        collected = self.incremental_collector(request)
        sealed_at = self.now()
        if collected.stock_series is not None and collected.stock_series.retrieved_at > sealed_at:
            raise ValueError("stock market-series retrieval cannot be after sealing")
        if any(
            benchmark.series is not None and benchmark.series.retrieved_at > sealed_at
            for benchmark in collected.benchmark_series
        ):
            raise ValueError("benchmark retrieval cannot be after sealing")
        (
            collection_summary,
            evidence_items,
            evidence_bindings,
        ) = normalize_incremental_collection(
            request,
            collected,
            sealed_at=sealed_at,
        )
        if {item.ref for item in evidence_items} & {item.ref for item in baseline_evidence.items}:
            raise EvidenceConflictError(
                "Incremental Evidence bundle must not copy Full Baseline Evidence references"
            )
        performance = calculate_stock_performance(request, collected.stock_series)
        stock_series_admitted = False
        if performance.stock.status is PerformanceComponentStatus.CALCULATED:
            market_result = next(
                result for result in collection_summary.domains if result.domain == "market"
            )
            binding_by_candidate_ref = {
                binding.candidate_ref: binding.admitted_ref for binding in evidence_bindings
            }
            linked_ref = binding_by_candidate_ref.get(collected.stock_series_evidence_ref)
            admitted_by_ref = {item.ref: item for item in evidence_items}
            linked_item = admitted_by_ref.get(linked_ref)
            calculation = performance.stock.calculation
            if (
                linked_item is None
                or linked_ref not in market_result.evidence_refs
                or collected.stock_series is None
                or calculation is None
                or not any(
                    source.source == collected.stock_series.source
                    and source.retrieved_at == collected.stock_series.retrieved_at
                    and source.fallback == collected.stock_series.fallback
                    for source in market_result.sources
                )
                or linked_item.source != collected.stock_series.source
                or linked_item.evidence_type != "adjusted_close"
                or linked_item.effective_date != calculation.end_session
                or isinstance(linked_item.value, bool)
                or not isinstance(linked_item.value, (int, float))
                or not math.isclose(
                    float(linked_item.value),
                    calculation.end_value,
                    rel_tol=1e-12,
                    abs_tol=1e-15,
                )
            ):
                raise ValueError(
                    "stock series advancement requires admitted current market Evidence"
                )
            stock_series_admitted = True
        performance = PerformanceObservation(
            stock=performance.stock,
            benchmarks=calculate_benchmark_performance(
                request,
                collected.benchmark_series,
            ),
        )
        research_availability = derive_research_availability(collection_summary)
        information_advancement = assess_information_advancement(
            baseline_items=baseline_evidence.items,
            current_items=evidence_items,
            performance=performance,
            stock_series_admitted=stock_series_admitted,
        )
        diagnostics = tuple(
            result.diagnostic
            for result in collection_summary.domains
            if result.diagnostic is not None
        )
        return (
            IncrementalCollectionPreflight(
                collection_summary=collection_summary,
                research_availability=research_availability,
                information_advancement=information_advancement,
                diagnostics=diagnostics,
            ),
            evidence_items,
            performance,
            sealed_at,
        )

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
        retained_settings = RunSettings.model_validate(retained.config_snapshot)
        retained_dataflow_config = retained_settings.dataflow_config(self.settings)
        retained_vendors = retained_dataflow_config.setdefault("data_vendors", {})
        if "instrument_eligibility" not in retained_vendors:
            current_vendors = self.settings.default_run_settings.dataflow_config(self.settings).get(
                "data_vendors", {}
            )
            if "instrument_eligibility" in current_vendors:
                retained_vendors["instrument_eligibility"] = current_vendors[
                    "instrument_eligibility"
                ]
        self._validate_instrument_eligibility(
            request,
            dataflow_config=retained_dataflow_config,
        )
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

    def _run_incremental_synthesis(
        self,
        synthesis_input: IncrementalSynthesisInput,
        *,
        run_settings: RunSettings,
        metrics: MetricsCallback,
        run_id: str,
        on_event: EventHandler | None,
    ) -> IncrementalSynthesis:
        """Use the run-scoped reasoning and serializer clients for required synthesis."""
        llms = self.llm_factory(run_settings, callbacks=[metrics])
        if isinstance(llms, RunLLMs):
            semantic_llm = llms.deep
            serializer_llm = llms.deep_serializer
        else:
            _quick_llm, semantic_llm = llms
            serializer_llm = semantic_llm

        def event_writer(raw: dict[str, Any]) -> None:
            self._persist_graph_event(run_id, raw, on_event)

        output_language = report_language_prompt_label(run_settings.output_language)
        semantic_prompt = (
            "Perform the required Incremental Research synthesis. Assess every Full "
            "Baseline Decision Component using only the typed input. Do not use sibling "
            "Incremental Nodes or invent Evidence. Limited or missing optional Research "
            "Availability alone must not create a Full Research Required reason; do not "
            "reintroduce required-coverage certification. Produce a concise analysis brief for "
            f"the strict serializer. Write all human-readable prose in {output_language}.\n\n"
            + synthesis_input.model_dump_json(indent=2)
        )
        with metrics.phase("incremental.synthesis.semantic", event_writer=event_writer):
            semantic_response = semantic_llm.invoke(
                semantic_prompt,
                config={"metadata": {"research_node": "incremental.synthesis.semantic"}},
            )
        semantic_brief = getattr(semantic_response, "content", semantic_response)
        serializer_prompt = (
            "Serialize a complete IncrementalSynthesis from this semantic brief and the "
            "typed bounded input. Every reassessment entry requires a concise reason; "
            "include Evidence references only when the permitted bundles support them. "
            "Limited or missing optional Research Availability alone must not create a Full "
            "Research Required reason, and required_coverage codes are forbidden. "
            "Use only the typed v1 reason codes for material thesis reversal, identity "
            "uncertainty, unreliable attribution, or material Evidence conflict. Write all "
            f"human-readable prose in {output_language}.\n\n"
            f"SEMANTIC BRIEF:\n{semantic_brief}\n\n"
            f"BOUNDED INPUT:\n{synthesis_input.model_dump_json(indent=2)}"
        )
        example = {
            "reassessment": {
                "entries": [
                    {
                        "component_id": "thesis",
                        "disposition": "reaffirmed",
                        "reason": "Explain the bounded reassessment.",
                    }
                ]
            },
            "decision": {"rating": "hold", "thesis": "Complete current decision."},
            "full_research_required_reasons": [],
        }
        with metrics.phase("incremental.synthesis.serialize", event_writer=event_writer):
            output = StructuredOutputRunner(
                llm=serializer_llm,
                schema=IncrementalSynthesis,
                validator=lambda value: value,
                node="incremental.synthesis.serialize",
                event_writer=event_writer,
                invoke_config={"metadata": {"research_node": "incremental.synthesis.serialize"}},
                repair_instructions=(
                    "Write all human-readable prose in "
                    f"{output_language}. Preserve IDs, enums, Evidence refs, and "
                    "typed collection limitations exactly."
                ),
            ).invoke(
                serializer_prompt,
                example=example,
                allowed_evidence_refs=tuple(
                    dict.fromkeys(
                        (
                            *synthesis_input.permitted_baseline_evidence_refs,
                            *(item.ref for item in synthesis_input.incremental_evidence.items),
                        )
                    )
                ),
            )
        return output.value

    @staticmethod
    def _validate_incremental_bundle_ownership(
        incremental_evidence: EvidenceBundle,
        baseline_evidence: EvidenceBundle,
    ) -> None:
        """Keep baseline Evidence out of an Incremental Run's owned bundle."""
        baseline_refs = {item.ref for item in baseline_evidence.items}
        if any(item.ref in baseline_refs for item in incremental_evidence.items):
            raise EvidenceConflictError(
                "Incremental Evidence bundle must not copy Full Baseline Evidence references"
            )

    @staticmethod
    def _validate_reassessment_closure(
        synthesis: IncrementalSynthesis,
        synthesis_input: IncrementalSynthesisInput,
    ) -> None:
        allowed_evidence_refs = set(synthesis_input.permitted_baseline_evidence_refs)
        allowed_evidence_refs.update(
            item.ref for item in synthesis_input.incremental_evidence.items
        )
        for entry in synthesis.reassessment.entries:
            if not set(entry.evidence_refs).issubset(allowed_evidence_refs):
                raise ValueError(
                    "Reassessment Evidence references must close over the baseline or current bundle"
                )

    @staticmethod
    def _validate_full_research_required_reason_closure(
        reasons: tuple[FullResearchRequiredReason, ...],
        synthesis_input: IncrementalSynthesisInput,
    ) -> None:
        allowed_evidence_refs = set(synthesis_input.permitted_baseline_evidence_refs)
        allowed_evidence_refs.update(
            item.ref for item in synthesis_input.incremental_evidence.items
        )
        for reason in reasons:
            if not set(reason.evidence_refs).issubset(allowed_evidence_refs):
                raise ValueError(
                    "Full Research Required Evidence references must close over the baseline or current bundle"
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
