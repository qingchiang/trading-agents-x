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
from tradingagents.dataflows.symbol_utils import (
    match_exchange_suffix,
    normalize_symbol,
)
from tradingagents.graph.research_graph import GraphExecution, ResearchGraph
from tradingagents.persistence import upgrade_database

from .contracts import (
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    ResearchArtifactDraft,
    RunEvent,
    RunExport,
    RunStatus,
)
from .eligibility import validate_instrument_eligibility
from .errors import (
    InstrumentEligibilityUnavailableError,
    UnsupportedInstrumentError,
)
from .exporting import (
    render_run_export_markdown,
    render_run_export_package,
)
from .instrument_names import resolve_local_instrument_name
from .llms import RunLLMs, create_run_llms
from .metrics import MetricsCallback
from .repository import RunRepository, RunView
from .runtime import RunCancelled, RunContext, WorkerShutdown
from .settings import AppSettings, RunSettings

logger = logging.getLogger(__name__)

EventHandler = Callable[[RunEvent], None]
EligibilityResolver = Callable[[str], Any]


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
        llm_factory: Callable[..., RunLLMs | tuple[Any, Any]] = (
            create_run_llms
        ),
        graph_factory: Callable[..., ResearchGraph] = ResearchGraph,
        identity_resolver: Callable[..., dict[str, str]] = resolve_instrument_identity,
        eligibility_resolver: EligibilityResolver = resolve_instrument_eligibility,
        local_name_resolver: Callable[[str, str, dict[str, Any]], str | None] = (
            resolve_local_instrument_name
        ),
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

    def enqueue(
        self,
        request: AnalysisRequest,
        *,
        idempotency_key: str | None = None,
        source_run_id: str | None = None,
    ) -> RunView:
        if not isinstance(request, AnalysisRequest):
            raise TypeError(
                "new Runs require an AnalysisRequest creation contract"
            )
        # Re-run the creation validators at the lifecycle seam.  A caller can
        # otherwise bypass Pydantic validation with ``model_construct`` and
        # hand the repository an invalid request that would still be durable.
        request = AnalysisRequest.model_validate(
            request.model_dump(mode="json", warnings=False)
        )
        run_settings = self.settings.resolve_run(request)
        dataflow_config = run_settings.dataflow_config(self.settings)
        self._validate_instrument_eligibility(
            request,
            dataflow_config=dataflow_config,
        )
        if source_run_id is not None:
            # A retained Run may carry a legacy request that is intentionally
            # readable but no longer admitted as a source for new research.
            source_request = self._creation_request_from_history(
                self.repository.get_run(source_run_id).request
            )
            self._validate_instrument_eligibility(
                source_request,
                dataflow_config=dataflow_config,
            )
        request = self.settings.materialize_request(
            request,
            run_settings=run_settings,
        )
        view, created = self.repository.create_run(
            request,
            run_settings.snapshot(),
            idempotency_key=idempotency_key,
            source_run_id=source_run_id,
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

    def _validate_instrument_eligibility(
        self,
        request: AnalysisRequest,
        *,
        dataflow_config: dict[str, Any] | None = None,
    ) -> None:
        """Fail closed unless one exact resolver result confirms an equity."""
        try:
            if dataflow_config is None:
                dataflow_config = (
                    self.settings.default_run_settings.dataflow_config(self.settings)
                )
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
                    memory = self.repository.memory_context(
                        request.ticker,
                        request.asset_type.value,
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
                    memory=memory,
                    instrument_context=instrument_context,
                    cancel_requested=lambda: self.repository.cancel_requested(
                        run.id
                    ),
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
                with SqliteSaver.from_conn_string(
                    str(self.settings.database_path)
                ) as saver:
                    saver.conn.execute("PRAGMA journal_mode=WAL")
                    saver.conn.execute(
                        f"PRAGMA busy_timeout={self.settings.busy_timeout_ms}"
                    )
                    saver.setup()
                    checkpoint_config = {
                        "configurable": {"thread_id": checkpoint_thread}
                    }
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
                            on_event=lambda raw: self._persist_graph_event(
                                run.id, raw, on_event
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
                    benchmark = self._benchmark(
                        request.ticker,
                        dataflow_config,
                    )
                    aggregate_metrics = self.repository.complete(
                        run.id,
                        result,
                        evidence=execution.evidence,
                        benchmark=benchmark,
                    )
                    result = result.model_copy(
                        update={"metrics": aggregate_metrics}
                    )
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
                    payload={
                        "metrics": aggregate_metrics.model_dump(mode="json")
                    },
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
                        "failed to persist terminal state for run %s "
                        "(analysis=%s persistence=%s)",
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
                            "message": (
                                "Analysis failed; inspect the server log."
                            ),
                            "metrics": aggregate_metrics.model_dump(mode="json"),
                        },
                        on_event=on_event,
                    )
                except Exception as event_exc:
                    logger.error(
                        "failed to persist failure event for run %s "
                        "(analysis=%s event=%s)",
                        run.id,
                        type(exc).__name__,
                        type(event_exc).__name__,
                    )
                raise

    def cancel(self, run_id: str) -> RunView:
        view = self.repository.request_cancel(run_id)
        self.repository.append_event(
            run_id,
            (
                "run.cancelled"
                if view.status is RunStatus.CANCELLED
                else "run.cancel_requested"
            ),
            payload={},
        )
        return view

    def retry(self, run_id: str) -> RunView:
        # Validate the retained request through the current creation contract
        # before mutating the retry lifecycle.  This keeps retry from becoming
        # an alternate execution path around current admission rules.
        retained = self.repository.get_run(run_id)
        request = self._creation_request_from_history(retained.request)
        run_settings = RunSettings.model_validate(retained.config_snapshot)
        self._validate_instrument_eligibility(
            request,
            dataflow_config=run_settings.dataflow_config(self.settings),
        )
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
        with SqliteSaver.from_conn_string(
            str(self.settings.database_path)
        ) as saver:
            saver.conn.execute(
                f"PRAGMA busy_timeout={self.settings.busy_timeout_ms}"
            )
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
