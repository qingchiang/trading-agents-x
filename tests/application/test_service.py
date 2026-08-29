from __future__ import annotations

import io
import json
import operator
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Barrier, Lock
from typing import Annotated, TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    PrimaryResearchCycleRecord,
    ResearchNodeRecord,
    RunRecord,
)
from tradingagents.application.errors import (
    IncrementalRequestConflictError,
    InvalidIncrementalBaselineError,
    UnsupportedInstrumentError,
)
from tradingagents.application.repository import (
    IdempotencyConflictError,
    InvalidRunTransitionError,
    RunRepository,
)
from tradingagents.application.runtime import RunCancelled, WorkerShutdown
from tradingagents.application.service import AnalysisService
from tradingagents.dataflows.config import get_config
from tradingagents.graph.research_graph import GraphExecution


def _equity_resolver(ticker: str) -> dict[str, str]:
    return {"symbol": ticker, "quote_type": "EQUITY"}


def test_first_full_run_commits_same_identity_node_and_primary_timeline(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
        now=lambda: datetime(2026, 7, 25, 1, 30, tzinfo=UTC),
    )

    result = service.run(AnalysisRequest(ticker="7203.T", analysis_date=date(2026, 7, 24)))
    timeline = repository.get_timeline("7203.T")
    run = repository.get_run(result.run_id)

    assert result.status is RunStatus.SUCCEEDED
    assert run.research_schema_version == "1"
    assert run.information_cutoff_at == datetime(2026, 7, 24, 14, 59, 59, 999999, tzinfo=UTC)
    assert run.method_snapshot["schema_version"] == "1"
    assert run.method_snapshot["research_schema_version"] == "1"
    assert run.method_snapshot["prompt_versions"]
    assert run.method_snapshot["enabled_roles"] == [
        "market",
        "social",
        "news",
        "fundamentals",
    ]
    assert set(run.method_snapshot["data_routes"]) == {
        "data_vendors",
        "tool_vendors",
        "data_vendors_by_market",
    }
    configured_routes = app_settings.default_run_settings.snapshot()["data_config"]
    assert run.method_snapshot["data_routes"] == {
        key: configured_routes[key]
        for key in ("data_vendors", "tool_vendors", "data_vendors_by_market")
    }
    assert run.method_snapshot["data_availability_policy"] == {
        "version": "1",
        "near_live_max_age_days": 5,
    }
    assert run.method_snapshot["thresholds"]["news_article_limit"] == 30
    assert len(run.method_snapshot["configuration_fingerprint"]) == 64
    assert timeline.primary_cycle_id == result.run_id
    assert [(node.id, node.cycle_id, node.is_primary) for node in timeline.all_nodes] == [
        (result.run_id, result.run_id, True)
    ]


def test_later_full_cycles_require_an_explicit_primary_choice_and_can_be_selected(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )

    first = service.run(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    with pytest.raises(ValueError, match="make_primary"):
        service.run(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    later = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            make_primary=False,
        )
    )
    assert [node.id for node in repository.get_timeline("NVDA").all_nodes] == [
        first.run_id,
        later.run_id,
    ]
    assert repository.get_timeline("NVDA").primary_cycle_id == first.run_id

    selected = repository.select_primary_cycle("NVDA", later.run_id)
    repeated = repository.select_primary_cycle("NVDA", later.run_id)

    assert selected.primary_cycle_id == later.run_id
    assert repeated == selected
    assert [node.is_primary for node in selected.all_nodes] == [
        node.id == later.run_id for node in selected.all_nodes
    ]


def test_completed_first_full_replays_before_later_full_primary_validation(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")

    first = service.enqueue(request, idempotency_key="first-full-replay")
    claimed = repository.claim_run(first.id, "worker", app_settings.lease_seconds)
    service.execute_claimed(claimed, worker_id="worker")

    replayed = service.enqueue(request, idempotency_key="first-full-replay")

    assert replayed.id == first.id
    assert replayed.is_research_node is True
    with pytest.raises(IdempotencyConflictError):
        service.enqueue(
            AnalysisRequest(ticker="NVDA", analysis_date="2026-07-23"),
            idempotency_key="first-full-replay",
        )


def test_incremental_request_requires_an_explicit_compatible_full_baseline(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))

    with pytest.raises(ValueError, match="full_baseline_run_id"):
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 24), research_kind="incremental")
    with pytest.raises(ValueError, match="must not carry a Full Baseline"):
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="full",
            full_baseline_run_id=baseline.run_id,
        )
    with pytest.raises(InvalidIncrementalBaselineError, match="same Instrument"):
        service.enqueue(
            AnalysisRequest(
                ticker="AAPL",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )


def test_incremental_slot_replays_identical_active_request_and_rejects_conflict(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    first = service.enqueue(request)
    assert service.enqueue(request).id == first.id

    with pytest.raises(IncrementalRequestConflictError):
        service.enqueue(request.model_copy(update={"analysts": ("market",)}))


def test_two_connections_return_one_incremental_slot_for_identical_requests(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        runs = list(executor.map(lambda _unused: service.enqueue(request), range(2)))

    assert {run.id for run in runs} == {runs[0].id}


@pytest.mark.parametrize(
    ("terminal_state", "expected_status", "trashed"),
    [
        ("failed", RunStatus.FAILED, False),
        ("cancelled", RunStatus.CANCELLED, False),
        ("trashed", RunStatus.CANCELLED, True),
    ],
)
def test_incremental_retry_keeps_inactive_history_when_a_conflicting_slot_is_active(
    app_settings,
    repository,
    terminal_state: str,
    expected_status: RunStatus,
    trashed: bool,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    failed_request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    inactive = service.enqueue(failed_request)
    if terminal_state == "failed":
        repository.claim_run(inactive.id, "fixture", app_settings.lease_seconds)
        repository.fail(inactive.id, RuntimeError("fixture failure"))
    else:
        service.cancel(inactive.id)
        if terminal_state == "trashed":
            repository.trash_runs((inactive.id,))
    active = service.enqueue(failed_request.model_copy(update={"analysts": ("market",)}))

    expected_error = (
        IncrementalRequestConflictError if terminal_state == "failed" else InvalidRunTransitionError
    )
    with pytest.raises(expected_error):
        service.retry(inactive.id)

    unchanged = repository.get_run(inactive.id)
    assert unchanged.status is expected_status
    assert unchanged.attempt == 1
    assert (unchanged.trashed_at is not None) is trashed
    assert repository.get_run(active.id).status is RunStatus.QUEUED


def test_incremental_retry_replays_an_identical_active_slot_only_from_failed_history(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    inactive = service.enqueue(request)
    repository.claim_run(inactive.id, "fixture", app_settings.lease_seconds)
    repository.fail(inactive.id, RuntimeError("fixture failure"))
    active = service.enqueue(request)
    with repository.sessions.begin() as session:
        active_record = session.get(RunRecord, active.id)
        assert active_record is not None
        active_record.status = RunStatus.SUCCEEDED.value
        session.add(
            ResearchNodeRecord(
                run_id=active.id,
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
                created_at=datetime.now(UTC).replace(tzinfo=None),
                incremental_products_json=None,
            )
        )

    replayed = service.retry(inactive.id)

    assert replayed.id == active.id
    assert replayed.is_research_node is True
    unchanged = repository.get_run(inactive.id)
    assert unchanged.status is RunStatus.FAILED
    assert unchanged.attempt == 1
    assert unchanged.trashed_at is None
    assert not any(
        event.event_type == "run.retry_queued" for event in repository.list_events(inactive.id)
    )


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_incremental_retry_rejects_an_active_target_before_slot_replay(
    app_settings,
    repository,
    status: RunStatus,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    target = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )
    if status is RunStatus.RUNNING:
        repository.claim_run(target.id, "fixture", app_settings.lease_seconds)

    events_before = repository.list_events(target.id)
    with pytest.raises(InvalidRunTransitionError, match="only failed runs"):
        service.retry(target.id)

    unchanged = repository.get_run(target.id)
    assert unchanged.status is status
    assert unchanged.attempt == 1
    assert repository.list_events(target.id) == events_before


def test_incremental_retry_maps_a_sqlite_slot_integrity_error_to_typed_conflict(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    failed = service.enqueue(request)
    repository.claim_run(failed.id, "fixture", app_settings.lease_seconds)
    repository.fail(failed.id, RuntimeError("fixture failure"))
    service.enqueue(request.model_copy(update={"analysts": ("market",)}))
    active_slot = repository._active_incremental_slot
    reads = 0

    def stale_first_slot_read(*args):
        nonlocal reads
        reads += 1
        if reads == 1:
            return None
        return active_slot(*args)

    monkeypatch.setattr(repository, "_active_incremental_slot", stale_first_slot_read)

    with pytest.raises(IncrementalRequestConflictError):
        service.retry(failed.id)

    assert reads == 2
    unchanged = repository.get_run(failed.id)
    assert unchanged.status is RunStatus.FAILED
    assert unchanged.attempt == 1


def test_two_sqlite_connections_make_one_incremental_retry_slot_winner(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    baseline = service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    failed = service.enqueue(request)
    repository.claim_run(failed.id, "fixture", app_settings.lease_seconds)
    repository.fail(failed.id, RuntimeError("fixture failure"))
    conflicting_request = request.model_copy(update={"analysts": ("market",)})
    barrier = Barrier(2)

    def attempt(operation):
        barrier.wait(timeout=10)
        try:
            return operation()
        except Exception as exc:  # Both service calls share the public error seam.
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                attempt,
                (
                    lambda: service.retry(failed.id),
                    lambda: service.enqueue(conflicting_request),
                ),
            )
        )

    successful = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
    conflicts = [
        outcome for outcome in outcomes if isinstance(outcome, IncrementalRequestConflictError)
    ]
    assert len(successful) == 1
    assert len(conflicts) == 1
    assert repository.get_run(successful[0].id).status is RunStatus.QUEUED
    retained = repository.get_run(failed.id)
    assert retained.status in {RunStatus.FAILED, RunStatus.QUEUED}
    assert retained.attempt in {1, 2}


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("backend_url", "https://changed-gateway.example.invalid/v1"),
        ("temperature", 0.7),
        ("llm_max_retries", 5),
    ],
)
def test_method_snapshot_records_resolved_llm_settings_in_its_fingerprint(
    app_settings,
    repository,
    field,
    changed_value,
) -> None:
    """Queued Runs retain non-secret LLM behavior that can change a method."""
    base_run_settings = app_settings.default_run_settings.model_copy(
        update={
            "backend_url": "https://gateway.example.invalid/v1",
            "temperature": 0.2,
            "llm_max_retries": 3,
            "data_config": {
                **app_settings.default_run_settings.data_config,
                "provider_api_key": "method-snapshot-test-secret",
            },
        }
    )
    base_settings = app_settings.model_copy(update={"default_run_settings": base_run_settings})
    request = AnalysisRequest(ticker="7203.T", analysis_date=date(2026, 7, 24))

    base_run = AnalysisService(
        base_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
    ).enqueue(request, idempotency_key=f"method-snapshot-base-{field}")
    base_snapshot = repository.get_run(base_run.id).method_snapshot

    assert base_snapshot["backend_url"] == "https://gateway.example.invalid/v1"
    assert base_snapshot["temperature"] == 0.2
    assert base_snapshot["llm_max_retries"] == 3
    assert "method-snapshot-test-secret" not in json.dumps(base_snapshot)

    changed_run_settings = base_run_settings.model_copy(update={field: changed_value})
    changed_settings = app_settings.model_copy(
        update={"default_run_settings": changed_run_settings}
    )
    changed_run = AnalysisService(
        changed_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
    ).enqueue(request, idempotency_key=f"method-snapshot-changed-{field}")
    changed_snapshot = repository.get_run(changed_run.id).method_snapshot

    assert changed_snapshot[field] == changed_value
    assert (
        changed_snapshot["configuration_fingerprint"] != base_snapshot["configuration_fingerprint"]
    )


def test_current_market_day_freezes_current_instant_and_future_rejects_without_run(
    app_settings,
    repository,
) -> None:
    now = datetime(2026, 7, 24, 15, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
        now=lambda: now,
    )

    run = service.enqueue(AnalysisRequest(ticker="7203.T", analysis_date=now.date()))
    assert run.information_cutoff_at == now.astimezone(UTC)

    with pytest.raises(ValueError, match="future analysis cutoff"):
        service.enqueue(AnalysisRequest(ticker="7203.T", analysis_date=date(2026, 7, 25)))
    assert repository.list_runs().total == 1


@pytest.mark.parametrize(
    "trigger",
    [
        "decision_insert",
        "node_insert",
        "primary_insert",
        "run_success",
        "attempt_success",
    ],
)
def test_atomic_research_commit_rolls_back_every_persisted_boundary(
    app_settings,
    repository,
    trigger,
) -> None:
    """SQLite failures leave the lifecycle in failed History, never partial Timeline."""
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    trigger_sql = {
        "decision_insert": """
            CREATE TRIGGER fail_decision_insert BEFORE INSERT ON decisions
            BEGIN SELECT RAISE(ABORT, 'injected decision failure'); END
        """,
        "node_insert": """
            CREATE TRIGGER fail_node_insert BEFORE INSERT ON research_nodes
            BEGIN SELECT RAISE(ABORT, 'injected node failure'); END
        """,
        "primary_insert": """
            CREATE TRIGGER fail_primary_insert BEFORE INSERT ON primary_research_cycles
            BEGIN SELECT RAISE(ABORT, 'injected primary failure'); END
        """,
        "run_success": """
            CREATE TRIGGER fail_run_success BEFORE UPDATE OF status ON runs
            WHEN NEW.status = 'succeeded'
            BEGIN SELECT RAISE(ABORT, 'injected run success failure'); END
        """,
        "attempt_success": """
            CREATE TRIGGER fail_attempt_success BEFORE UPDATE OF status ON run_attempts
            WHEN NEW.status = 'succeeded'
            BEGIN SELECT RAISE(ABORT, 'injected attempt success failure'); END
        """,
    }[trigger]
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(trigger_sql)

    with pytest.raises(Exception, match="injected"):
        service.run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 24)))

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    with repository.sessions() as session:
        assert session.query(DecisionRecord).count() == 0
        assert session.query(ResearchNodeRecord).count() == 0
        assert session.query(PrimaryResearchCycleRecord).count() == 0
    assert repository.get_timeline("NVDA").all_nodes == ()


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("NVDA", datetime(2026, 7, 25, 3, 59, 59, 999999, tzinfo=UTC)),
        ("7203.T", datetime(2026, 7, 24, 14, 59, 59, 999999, tzinfo=UTC)),
        ("600000.SS", datetime(2026, 7, 24, 15, 59, 59, 999999, tzinfo=UTC)),
    ],
)
def test_historical_cutoffs_use_each_listed_instrument_market_day_end(
    app_settings,
    repository,
    ticker,
    expected,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
        now=lambda: datetime(2026, 7, 26, tzinfo=UTC),
    )

    run = service.enqueue(AnalysisRequest(ticker=ticker, analysis_date="2026-07-24"))

    assert run.information_cutoff_at == expected


def _execution(ticker: str) -> GraphExecution:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fixture evidence",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Fixture evidence.",
    )
    bundle = EvidenceBundle(
        instrument=ticker,
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    report = analyst_report(
        executive_summary="Fixture summary.",
        confidence=0.8,
        evidence_ref=item.ref,
        narrative="Fixture report.",
    )
    decision = research_decision(
        confidence=0.6,
        thesis="Fixture thesis.",
        evidence_refs=(item.ref,),
    )
    return GraphExecution(
        state={},
        evidence=bundle,
        reports={"market": report},
        decision=decision,
    )


class _Graph:
    barrier: Barrier | None = None
    observed: list[tuple[str, str, str]] = []
    lock = Lock()
    error: Exception | None = None

    def __init__(self, **_kwargs):
        pass

    def execute(self, context, *, on_event, **_kwargs):
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        with self.lock:
            self.observed.append(
                (
                    context.request.ticker,
                    context.settings.llm_provider,
                    get_config()["llm_provider"],
                )
            )
        on_event(
            {
                "event_type": "node.completed",
                "node": "fixture",
                "payload": {"api_key": "must-not-persist"},
            }
        )
        if self.error is not None:
            raise self.error
        return _execution(context.request.ticker)


class _ArtifactGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(self, context, **_kwargs):
        execution = _execution(context.request.ticker)
        context.artifact_writer(
            ResearchArtifactDraft(
                node="analyst.market",
                stage="analyst",
                role="market",
                generation_method=ArtifactGenerationMethod.TOOL_CALL,
                content=execution.reports["market"],
            )
        )
        return execution


class _DecisionlessGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(self, context, **_kwargs):
        execution = _execution(context.request.ticker)
        return GraphExecution(
            state=execution.state,
            evidence=execution.evidence,
            reports=execution.reports,
            decision=None,
        )


def test_failed_atomic_full_commit_keeps_execution_history_without_node_or_decision(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_DecisionlessGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )

    with pytest.raises(ValueError, match="complete Research Decision"):
        service.run(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    failed = repository.list_runs(status=RunStatus.FAILED).items[0]
    assert repository.get_timeline("NVDA").all_nodes == ()
    assert repository.get_result(failed.id).decision is None


def test_queued_legacy_run_fails_execution_boundary_without_a_node(
    app_settings,
    repository,
) -> None:
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    run, _ = repository.create_run(request, {"fixture": True})
    claimed = repository.claim_run(run.id, "worker", 30)
    service = AnalysisService(
        app_settings,
        repository=repository,
        eligibility_resolver=_equity_resolver,
    )

    with pytest.raises(ValueError, match="legacy runs"):
        service.execute_claimed(claimed, worker_id="worker")

    assert repository.get_run(run.id).status is RunStatus.FAILED
    assert repository.get_timeline("NVDA").all_nodes == ()


class _MetricFailureGraph:
    def __init__(self, *, metrics, **_kwargs):
        self.metrics = metrics

    def execute(self, _context, **_kwargs):
        run_id = uuid4()
        self.metrics.on_llm_start(
            {},
            ["fixture"],
            run_id=run_id,
            metadata={"research_node": "analyst.market.serialize.core"},
        )
        self.metrics.on_llm_end(
            LLMResult(
                generations=[
                    [
                        ChatGeneration(
                            message=AIMessage(
                                content="fixture",
                                usage_metadata={
                                    "input_tokens": 250,
                                    "output_tokens": 25,
                                    "total_tokens": 275,
                                },
                            )
                        )
                    ]
                ]
            ),
            run_id=run_id,
        )
        raise RuntimeError("fixture structured output failure")


class _CheckpointState(TypedDict):
    completed: Annotated[list[str], operator.add]


class _ResumableGraph:
    first_calls = 0
    second_calls = 0
    fail_second_once = True

    def __init__(self, **_kwargs):
        pass

    def execute(
        self,
        context,
        *,
        checkpointer,
        checkpoint_thread_id,
        resume,
        on_event,
    ):
        def first_node(_state):
            type(self).first_calls += 1
            execution = _execution(context.request.ticker)
            context.artifact_writer(
                ResearchArtifactDraft(
                    node="fixture.first",
                    stage="analyst",
                    role="market",
                    generation_method=ArtifactGenerationMethod.TOOL_CALL,
                    content=execution.reports["market"],
                )
            )
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.first",
                    "payload": {},
                }
            )
            return {"completed": ["first"]}

        def second_node(_state):
            type(self).second_calls += 1
            if type(self).fail_second_once:
                type(self).fail_second_once = False
                raise RuntimeError("fixture crash after first checkpoint")
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.second",
                    "payload": {},
                }
            )
            return {"completed": ["second"]}

        workflow = StateGraph(_CheckpointState)
        workflow.add_node("first", first_node)
        workflow.add_node("second", second_node)
        workflow.add_edge(START, "first")
        workflow.add_edge("first", "second")
        workflow.add_edge("second", END)
        graph = workflow.compile(checkpointer=checkpointer)
        state = graph.invoke(
            None if resume else {"completed": []},
            config={"configurable": {"thread_id": checkpoint_thread_id}},
        )
        assert state["completed"] == ["first", "second"]
        return _execution(context.request.ticker)


class _CancellingCheckpointGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(
        self,
        context,
        *,
        checkpointer,
        checkpoint_thread_id,
        resume,
        on_event,
    ):
        def first_node(_state):
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.before_cancel",
                    "payload": {},
                }
            )
            return {"completed": ["first"]}

        def cancel_node(_state):
            raise RunCancelled("fixture cooperative cancellation")

        workflow = StateGraph(_CheckpointState)
        workflow.add_node("first", first_node)
        workflow.add_node("cancel", cancel_node)
        workflow.add_edge(START, "first")
        workflow.add_edge("first", "cancel")
        workflow.add_edge("cancel", END)
        graph = workflow.compile(checkpointer=checkpointer)
        graph.invoke(
            None if resume else {"completed": []},
            config={"configurable": {"thread_id": checkpoint_thread_id}},
        )
        raise AssertionError("cancelling graph must not complete")


@pytest.fixture(autouse=True)
def _reset_graph():
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None
    _ResumableGraph.first_calls = 0
    _ResumableGraph.second_calls = 0
    _ResumableGraph.fail_second_once = True
    yield
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None
    _ResumableGraph.first_calls = 0
    _ResumableGraph.second_calls = 0
    _ResumableGraph.fail_second_once = True


def _service(
    app_settings,
    repository: RunRepository,
    graph_factory=_Graph,
) -> AnalysisService:
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=graph_factory,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
    )


def test_service_persists_events_before_callback_and_result(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    seen = []

    def callback(event):
        persisted = repository.list_events(
            event.run_id,
            after_sequence=event.sequence - 1,
        )
        assert persisted[0] == event
        seen.append(event.event_type)

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        ),
        on_event=callback,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.evidence is not None
    assert seen == [
        "run.started",
        "node.completed",
        "evidence.sealed",
        "run.succeeded",
    ]
    events = repository.list_events(result.run_id)
    assert events[0].event_type == "run.queued"
    assert events[-1].event_type == "run.succeeded"
    assert events[2].payload["api_key"] == "[REDACTED]"


def test_full_service_run_has_no_legacy_review_state(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.decision is not None
    with repository.engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "outcomes" not in tables
    assert "reflections" not in tables


def test_rejected_creation_has_no_persistent_side_effects(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    invalid_request = AnalysisRequest.model_construct(
        ticker="BTC-USD",
        analysis_date=date(2026, 7, 24),
        asset_type="crypto",
    )

    with pytest.raises(
        ValueError,
        match="stock|Crypto instruments|listed equity",
    ):
        service.enqueue(invalid_request)

    table_names = (
        "runs",
        "run_attempts",
        "run_events",
        "run_evidence",
        "run_artifacts",
        "decisions",
        "checkpoints",
        "writes",
    )
    with repository.engine.connect() as connection:
        available_tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        counts = {
            table: (
                connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
                if table in available_tables
                else 0
            )
            for table in table_names
        }

    assert counts == dict.fromkeys(table_names, 0)


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (
            {
                "short_name": "NVIDIA",
                "company_name": "NVIDIA Corporation",
                "long_name": "NVIDIA Corporation",
                "name": "Fallback name",
            },
            "NVIDIA",
        ),
        ({"company_name": "Toyota Motor Corporation"}, "Toyota Motor Corporation"),
        ({"long_name": "Mitsubishi Heavy Industries"}, "Mitsubishi Heavy Industries"),
        ({"name": "Fallback name"}, "Fallback name"),
        ({"short_name": " n/a ", "company_name": "  "}, None),
    ],
)
def test_service_persists_preferred_instrument_display_name(
    app_settings,
    repository,
    identity,
    expected,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda _ticker, _date: identity,
        eligibility_resolver=_equity_resolver,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.instrument_name == expected
    assert repository.get_run(result.run_id).instrument_name == expected
    assert repository.get_result(result.run_id).instrument_name == expected


def test_service_persists_cutoff_safe_local_name_once(
    app_settings,
    repository,
) -> None:
    observed: list[tuple[str, str]] = []

    def local_name(ticker, analysis_date, _config):
        observed.append((ticker, analysis_date))
        return "トヨタ自動車"

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda _ticker, _date: {"company_name": "Toyota Motor Corporation"},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=local_name,
    )

    result = service.run(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.instrument_local_name == "トヨタ自動車"
    assert repository.get_run(result.run_id).instrument_local_name == "トヨタ自動車"
    assert repository.get_result(result.run_id).instrument_local_name == "トヨタ自動車"
    assert observed == [("7203.T", "2026-07-24")]


def test_instrument_identity_failure_does_not_fail_research_run(
    app_settings,
    repository,
    caplog,
) -> None:
    def fail_identity(_ticker, _date):
        raise RuntimeError("private resolver details")

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=fail_identity,
        eligibility_resolver=_equity_resolver,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.instrument_name is None
    assert "RuntimeError" in caplog.text
    assert "private resolver details" not in caplog.text


def test_service_commits_artifact_and_event_before_callback(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ArtifactGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    observed = []

    def callback(event):
        if event.event_type != "artifact.created":
            return
        artifacts = repository.list_artifacts(event.run_id)
        assert [artifact.id for artifact in artifacts] == [event.payload["artifact_id"]]
        persisted = repository.list_events(
            event.run_id,
            after_sequence=event.sequence - 1,
        )
        assert persisted[0] == event
        observed.append(event)

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        ),
        on_event=callback,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(observed) == 1
    assert set(observed[0].payload) == {
        "artifact_id",
        "attempt",
        "stage",
        "role",
        "round",
        "schema_version",
        "prompt_version",
        "generation_method",
        "generation_observations",
        "content_type",
    }


def test_artifact_persistence_failure_fails_attempt_and_retains_checkpoint(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ArtifactGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    def fail_write(*_args, **_kwargs):
        raise OSError("artifact database unavailable")

    monkeypatch.setattr(repository, "append_artifact", fail_write)

    with pytest.raises(OSError, match="artifact database unavailable"):
        service.execute_claimed(claimed, worker_id="worker")

    assert repository.get_run(queued.id).status is RunStatus.FAILED
    assert repository.checkpoint_thread(queued.id) == checkpoint


def test_concurrent_runs_do_not_cross_provider_configuration(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.barrier = Barrier(2)
    requests = (
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
            llm_provider="openai",
        ),
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
            llm_provider="deepseek",
            output_language="ja",
        ),
    )
    claimed = []
    for index, request in enumerate(requests):
        queued = service.enqueue(request)
        claimed.append(repository.claim_run(queued.id, f"worker-{index}", 30))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda pair: service.execute_claimed(
                    pair[1],
                    worker_id=f"worker-{pair[0]}",
                ),
                enumerate(claimed),
            )
        )

    assert {result.status for result in results} == {RunStatus.SUCCEEDED}
    assert set(_Graph.observed) == {
        ("NVDA", "openai", "openai"),
        ("7203.T", "deepseek", "deepseek"),
    }


def test_failure_is_redacted_and_checkpoint_is_retained(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = RuntimeError("provider token=private-value")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    with pytest.raises(RuntimeError):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert "private-value" not in failed.error_message
    assert repository.checkpoint_thread(queued.id) == checkpoint


def test_snapshot_conversion_failure_fails_claimed_run_before_graph(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    with repository.sessions.begin() as session:
        record = session.get(RunRecord, queued.id)
        record.request_json = {
            **record.request_json,
            "ticker": "BTC-USD",
            "asset_type": "crypto",
        }
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(UnsupportedInstrumentError, match="not a supported listed equity"):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert failed.error_code == "UnsupportedInstrumentError"
    assert "not a supported listed equity" in failed.error_message
    assert repository.list_attempts(queued.id)[0].status is RunStatus.FAILED
    events = repository.list_events(queued.id)
    assert events[-1].event_type == "run.failed"
    assert events[-1].payload["error_code"] == "UnsupportedInstrumentError"
    assert [event.event_type for event in events] == [
        "run.queued",
        "run.started",
        "run.failed",
    ]


def test_failure_persists_observed_metrics_and_emits_the_aggregate(
    app_settings,
    repository,
) -> None:
    service = _service(
        app_settings,
        repository,
        graph_factory=_MetricFailureGraph,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(RuntimeError, match="structured output failure"):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    event = repository.list_events(queued.id)[-1]
    assert failed.metrics.llm_calls == 1
    assert failed.metrics.input_tokens == 250
    assert failed.metrics.output_tokens == 25
    assert failed.metrics.node_metrics["analyst.market.serialize.core"].llm_calls == 1
    assert event.event_type == "run.failed"
    assert event.payload["metrics"]["input_tokens"] == 250

    _, json_body = service.export(queued.id, format="json")
    json_payload = json.loads(json_body)
    assert json_payload["run"]["metrics"]["input_tokens"] == 250
    assert json_payload["attempts"][0]["status"] == "failed"
    assert json_payload["attempts"][0]["metrics"]["input_tokens"] == 250

    _, markdown_body = service.export(queued.id, format="markdown")
    assert "## Performance" in markdown_body
    assert "| 1 | failed |" in markdown_body
    assert "Input tokens: `250`" in markdown_body

    _, package_body = service.export(queued.id, format="package")
    with zipfile.ZipFile(io.BytesIO(package_body)) as archive:
        run_payload = json.loads(archive.read("run.json"))
    assert run_payload["attempts"][0]["status"] == "failed"
    assert run_payload["run"]["metrics"]["input_tokens"] == 250


def test_cooperative_cancel_cleans_checkpoint(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = RunCancelled("cancelled")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    cleared = []
    monkeypatch.setattr(service, "_clear_checkpoint", cleared.append)

    result = service.execute_claimed(claimed, worker_id="worker")

    assert result.status is RunStatus.CANCELLED
    assert cleared == [repository.checkpoint_thread(queued.id)]
    assert repository.get_run(queued.id).status is RunStatus.CANCELLED


def test_worker_shutdown_requeues_run_and_preserves_checkpoint(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = WorkerShutdown("fixture shutdown")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    with pytest.raises(WorkerShutdown):
        service.execute_claimed(
            claimed,
            worker_id="worker",
            shutdown_requested=lambda: True,
        )

    released = repository.get_run(queued.id)
    assert released.status is RunStatus.QUEUED
    assert released.attempt == 1
    assert repository.checkpoint_thread(queued.id) == checkpoint
    assert repository.list_events(queued.id)[-1].event_type == "run.interrupted"


def test_queued_cancel_is_terminal_and_emits_event(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    queued = service.enqueue(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    cancelled = service.cancel(queued.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert repository.list_events(queued.id)[-1].event_type == "run.cancelled"


def test_retry_resumes_real_langgraph_checkpoint_and_success_cleans_it(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ResumableGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    checkpoint_thread = repository.checkpoint_thread(queued.id)
    claimed = repository.claim_run(queued.id, "worker-1", 30)

    with pytest.raises(RuntimeError, match="after first checkpoint"):
        service.execute_claimed(claimed, worker_id="worker-1")

    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        checkpoint_config = {"configurable": {"thread_id": checkpoint_thread}}
        assert saver.get_tuple(checkpoint_config) is not None

    retried = service.retry(queued.id)
    assert retried.attempt == 2
    assert repository.checkpoint_thread(queued.id) == checkpoint_thread
    claimed_again = repository.claim_run(queued.id, "worker-2", 30)
    result = service.execute_claimed(claimed_again, worker_id="worker-2")

    assert result.status is RunStatus.SUCCEEDED
    assert _ResumableGraph.first_calls == 1
    assert _ResumableGraph.second_calls == 2
    artifacts = repository.list_artifacts(queued.id)
    assert len(artifacts) == 1
    assert artifacts[0].attempt == 1
    assert "run.resumed" in {event.event_type for event in repository.list_events(queued.id)}
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        assert saver.get_tuple(checkpoint_config) is None


def test_cooperative_cancel_deletes_real_pending_checkpoint(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_CancellingCheckpointGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    checkpoint_thread = repository.checkpoint_thread(queued.id)
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = service.execute_claimed(claimed, worker_id="worker")

    assert result.status is RunStatus.CANCELLED
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        assert saver.get_tuple({"configurable": {"thread_id": checkpoint_thread}}) is None


@pytest.mark.parametrize("format", ("markdown", "json", "package"))
def test_service_export_reads_the_durable_result(
    format,
    app_settings,
    repository,
) -> None:
    service = _service(
        app_settings,
        repository,
        graph_factory=_ArtifactGraph,
    )
    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    repository.append_event(
        result.run_id,
        "node.output_retry",
        node="debate.agenda.serialize",
        payload={
            "method": "tool_call_recovered",
            "reason_code": "non_json_response",
            "validation_issues": ["schema.issues"],
        },
    )
    repository.append_event(
        result.run_id,
        "node.output_recovered",
        node="debate.agenda.serialize",
        payload={"method": "tool_call_recovered"},
    )

    media_type, body = service.export(result.run_id, format=format)

    expected_media_type = {
        "markdown": "text/markdown",
        "json": "application/json",
        "package": "application/zip",
    }[format]
    assert media_type.startswith(expected_media_type)
    if format == "package":
        assert isinstance(body, bytes)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            report = archive.read("report.md").decode()
            assert result.run_id in report
            assert "Fixture thesis" in report
            run_payload = json.loads(archive.read("run.json"))
            assert run_payload["attempts"][0]["status"] == "succeeded"
            assert run_payload["result"]["recoveries"][0]["node"] == ("debate.agenda.serialize")
            assert "## Structured Recoveries" in report
        return
    assert isinstance(body, str)
    assert result.run_id in body
    assert "Fixture thesis" in body
    if format == "json":
        payload = json.loads(body)
        assert payload["schema_version"] == "11"
        assert payload["run"]["id"] == result.run_id
        assert payload["attempts"][0]["status"] == "succeeded"
        assert payload["attempts"][0]["metrics"] == payload["run"]["metrics"]
        assert payload["result"]["evidence"] == payload["evidence"]
        assert payload["result"]["recoveries"][0]["initial_reason_code"] == ("non_json_response")
        assert payload["evidence"]["items"][0]["source"] == "fixture"
        assert payload["artifacts"][0]["stage"] == "analyst"
        content = payload["artifacts"][0]["content"]
        assert "Fixture report." in content["markdown"]
    else:
        assert "## Research Process" in body
        assert "### analyst · market · round 0" not in body
        assert "## Reports" in body
        assert "Fixture report." in body
        assert "_No deliberation artifacts were recorded for this run._" in body
        assert "## Warnings" in body
        assert "## Structured Recoveries" in body
        assert "`debate.agenda.serialize`" in body
        assert "## Sources" in body
        assert "### Attempts" in body
        assert "| 1 | succeeded |" in body
        assert "### E01" in body
        assert '"source": "fixture"' in body


def test_service_export_rejects_unknown_format(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    with pytest.raises(ValueError, match="markdown.*json"):
        service.export(result.run_id, format="pdf")
