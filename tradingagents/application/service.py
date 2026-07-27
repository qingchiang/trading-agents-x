"""Application service owning the complete analysis lifecycle."""

from __future__ import annotations

import json
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
    RunEvent,
    RunStatus,
)
from .llms import create_run_llms
from .metrics import MetricsCallback
from .repository import RunRepository, RunView
from .runtime import RunCancelled, RunContext
from .settings import AppSettings, RunSettings

logger = logging.getLogger(__name__)

EventHandler = Callable[[RunEvent], None]


class AnalysisService:
    """The only component allowed to coordinate graph and durable state."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        repository: RunRepository | None = None,
        llm_factory: Callable[..., tuple[Any, Any]] = create_run_llms,
        graph_factory: Callable[..., ResearchGraph] = ResearchGraph,
        identity_resolver: Callable[..., dict[str, str]] = resolve_instrument_identity,
    ):
        self.settings = settings
        if repository is None:
            upgrade_database(settings)
        self.repository = repository or RunRepository(settings)
        self.llm_factory = llm_factory
        self.graph_factory = graph_factory
        self.identity_resolver = identity_resolver

    def enqueue(
        self,
        request: AnalysisRequest,
        *,
        idempotency_key: str | None = None,
    ) -> RunView:
        run_settings = self.settings.resolve_run(request)
        view, created = self.repository.create_run(
            request,
            run_settings.snapshot(),
            idempotency_key=idempotency_key,
        )
        if created:
            self.repository.append_event(
                view.id,
                "run.queued",
                payload={
                    "profile": request.profile.value,
                    "ticker": request.ticker,
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

    def execute_claimed(
        self,
        run: RunView,
        *,
        worker_id: str,
        on_event: EventHandler | None = None,
    ) -> AnalysisResult:
        if run.status is not RunStatus.RUNNING:
            raise ValueError(f"run {run.id} must be claimed before execution")
        run_settings = RunSettings.model_validate(run.config_snapshot)
        legacy_config = run_settings.legacy_config(self.settings)
        validate_market_routing(legacy_config)
        checkpoint_thread = self.repository.checkpoint_thread(run.id)
        self._emit(
            run.id,
            "run.started",
            payload={"attempt": run.attempt},
            on_event=on_event,
        )
        metrics = MetricsCallback()

        with self._heartbeat(run.id, worker_id):
            try:
                with use_config(legacy_config):
                    identity = self.identity_resolver(
                        run.request.ticker,
                        run.request.analysis_date.isoformat(),
                    )
                    instrument_context = build_instrument_context(
                        run.request.ticker,
                        run.request.asset_type.value,
                        identity,
                    )
                    memory = self.repository.memory_context(
                        run.request.ticker,
                        run.request.asset_type.value,
                    )
                    quick_llm, deep_llm = self.llm_factory(
                        run_settings,
                        callbacks=[metrics],
                    )
                graph = self.graph_factory(
                    quick_llm=quick_llm,
                    deep_llm=deep_llm,
                    profile=run.request.profile,
                    selected_analysts=run.request.analysts,
                    metrics=metrics,
                )
                context = RunContext(
                    run_id=run.id,
                    request=run.request,
                    settings=run_settings,
                    legacy_config=legacy_config,
                    past_context=memory,
                    instrument_context=instrument_context,
                    cancel_requested=lambda: self.repository.cancel_requested(
                        run.id
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
                    with use_config(legacy_config):
                        execution = graph.execute(
                            context,
                            checkpointer=saver,
                            checkpoint_thread_id=checkpoint_thread,
                            resume=resume,
                            on_event=lambda raw: self._persist_graph_event(
                                run.id, raw, on_event
                            ),
                        )
                    result = self._result(run.id, execution, metrics)
                    benchmark = self._benchmark(
                        run.request.ticker,
                        legacy_config,
                    )
                    self.repository.complete(
                        run.id,
                        result,
                        evidence=execution.evidence,
                        benchmark=benchmark,
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
                self.repository.finish_cancel(run.id)
                self._clear_checkpoint(checkpoint_thread)
                self._emit(
                    run.id,
                    "run.cancelled",
                    payload={},
                    on_event=on_event,
                )
                return AnalysisResult(
                    run_id=run.id,
                    status=RunStatus.CANCELLED,
                    instrument=run.request.ticker,
                    reports={},
                    decision=None,
                    metrics=metrics.snapshot(),
                    warnings=("Run cancelled at a graph node boundary.",),
                )
            except Exception as exc:
                self.repository.fail(run.id, exc)
                self._emit(
                    run.id,
                    "run.failed",
                    payload={
                        "error_code": type(exc).__name__,
                        "message": "Analysis failed; inspect the server log.",
                    },
                    on_event=on_event,
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
        view = self.repository.retry(run_id)
        self.repository.append_event(
            run_id,
            "run.retry_queued",
            payload={"attempt": view.attempt},
        )
        return view

    def rerun(self, run_id: str) -> RunView:
        view = self.repository.rerun(run_id)
        self.repository.append_event(
            view.id,
            "run.queued",
            payload={"rerun_of": run_id},
        )
        return view

    def export(
        self,
        run_id: str,
        *,
        format: str = "markdown",
    ) -> tuple[str, str]:
        result = self.repository.get_result(run_id)
        if format == "json":
            return (
                "application/json",
                result.model_dump_json(indent=2),
            )
        if format != "markdown":
            raise ValueError("format must be 'markdown' or 'json'")
        sections = [
            f"# TradingAgentsX Research: {result.instrument}",
            "",
            f"- Run: `{result.run_id}`",
            f"- Status: `{result.status.value}`",
        ]
        for name, report in result.reports.items():
            narrative = getattr(report, "narrative", str(report))
            sections.extend(["", f"## {name.title()}", "", narrative])
        if result.decision:
            sections.extend(
                [
                    "",
                    "## Research Decision",
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
        return "text/markdown; charset=utf-8", "\n".join(sections)

    def backup_database(self, destination: Path) -> Path:
        return self.repository.backup(destination)

    def _result(
        self,
        run_id: str,
        execution: GraphExecution,
        metrics: MetricsCallback,
    ) -> AnalysisResult:
        warnings = tuple(
            dict.fromkeys(
                warning
                for report in execution.reports.values()
                for warning in report.warnings
            )
        )
        return AnalysisResult(
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            instrument=execution.evidence.instrument,
            reports=execution.reports,
            decision=execution.decision,
            metrics=metrics.snapshot(),
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
