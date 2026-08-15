"""Transactional repository facade for application persistence."""

from __future__ import annotations

from ._repository_common import (
    ADJUSTMENT_SEMANTICS,
    HORIZON_LIMIT,
    METHOD_CATEGORY,
    METHOD_VERSION,
    OBSERVATION_LIMITATIONS,
    PRICE_SEMANTICS,
    UTC,
    AnalysisRequest,
    AnalysisResult,
    AnalystReport,
    Any,
    AppSettings,
    ArtifactConflictError as ArtifactConflictError,
    ArtifactGenerationMethod,
    ArtifactGenerationObservation,
    Base,
    CoverageAttestation,
    CurrentResearchState,
    DebateAgenda,
    DecisionBrief,
    DecisionRecord,
    EffectiveEvidenceSnapshot,
    EvidenceBundle,
    EvidenceConflictError,
    EvidenceNotSealedError,
    EvidenceSealView,
    IdempotencyConflictError,
    IndeterminateReason,
    InvalidResearchBaselineError,
    InvalidRunTransitionError,
    JudgeDraft,
    MemoryContext,
    OutcomeFeedbackRecord,
    OutcomeFeedbackRetirementConflictError as OutcomeFeedbackRetirementConflictError,
    OutcomeFeedbackRetirementNotFoundError as OutcomeFeedbackRetirementNotFoundError,
    OutcomeFeedbackRetirementReason,
    OutcomeObservationStatus,
    OutcomeRecord,
    OutcomeReflectionDraft,
    OutcomeReflectionRegenerationConflictError as OutcomeReflectionRegenerationConflictError,
    OutcomeReflectionRegenerationNotFoundError as OutcomeReflectionRegenerationNotFoundError,
    Path,
    RebuttalReview,
    RecentInstrument,
    ReflectionAttemptRecord,
    ReflectionGenerationCycleRecord,
    ReflectionRecord,
    ResearchArtifact,
    ResearchArtifactDraft,
    ResearchCase,
    ResearchChain,
    ResearchChainNotFoundError,
    ResearchChainRecord,
    ResearchChangeConclusion,
    ResearchDecision,
    ResearchExecutionStrategy,
    ResearchRating,
    ResearchRevision,
    ResearchRevisionDraft,
    ResearchRevisionNotFoundError as ResearchRevisionNotFoundError,
    ResearchRevisionRecord,
    ResearchRevisionRole,
    ResearchUpdateAudit,
    RevisionDelta,
    RiskReview,
    RunAttemptRecord,
    RunAttemptView,
    RunEvent,
    RunEvidenceRecord,
    RunMetrics,
    RunNotFoundError,
    RunPage,
    RunRecord,
    RunStatus,
    RunSummaryView,
    RunTrashState,
    RunView,
    Session,
    StructuredRecoveryNotice,
    UpdateSummary,
    __version__,
    _aware,
    _invalid_candidate_audit,
    _sanitize_payload,
    _usage_int,
    _utc_naive,
    create_sqlite_engine,
    datetime,
    delete,
    earliest_outcome_check_at,
    func,
    market_timezone,
    merge_run_metrics,
    qualify_reflection,
    select,
    sessionmaker,
    sqlite3,
    uuid4,
)
from ._repository_outcome import OutcomeReviewStore
from ._repository_research import ResearchChainStore
from ._repository_run import RunEventEvidenceStore


class RunRepository:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        settings.prepare_filesystem()
        self.engine = create_sqlite_engine(
            settings.database_path,
            busy_timeout_ms=settings.busy_timeout_ms,
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, class_=Session)
        self.run_store = RunEventEvidenceStore(self)
        self.outcome_store = OutcomeReviewStore(self)
        self.research_store = ResearchChainStore(self)

    def create_schema(self) -> None:
        """Create the current schema for tests; production entry points run Alembic."""
        Base.metadata.create_all(self.engine)

    @staticmethod
    def _qualify_reflection(**kwargs: Any):
        """Keep the historical repository patch seam while delegating storage."""
        return qualify_reflection(**kwargs)

    @staticmethod
    def _now() -> datetime:
        """Keep the historical repository clock seam across aggregate stores."""
        return _utc_naive()

    def create_run(
        self,
        request: AnalysisRequest,
        config_snapshot: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        source_run_id: str | None = None,
        research_chain_requested: bool = False,
    ) -> tuple[RunView, bool]:
        return self.run_store.create_run(
            request,
            config_snapshot,
            idempotency_key=idempotency_key,
            source_run_id=source_run_id,
            research_chain_requested=research_chain_requested,
        )

    def create_chain_update(
        self,
        chain_id: str,
        baseline_revision_id: str,
        request: AnalysisRequest,
        config_snapshot: dict[str, Any],
        *,
        execution_strategy: ResearchExecutionStrategy,
        idempotency_key: str | None = None,
    ) -> tuple[RunView, bool]:
        """Atomically create or resolve one Full update for the current head."""
        now = _utc_naive()
        request_json = request.model_dump(mode="json")
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            chain = session.get(ResearchChainRecord, chain_id)
            if chain is None:
                raise ResearchChainNotFoundError(chain_id)
            baseline = session.get(ResearchRevisionRecord, baseline_revision_id)
            if baseline is None or baseline.chain_id != chain.id:
                raise InvalidResearchBaselineError(
                    "update baseline must belong to the same Research Chain"
                )
            if request.ticker != chain.instrument:
                raise InvalidResearchBaselineError(
                    "update Instrument must match the Research Chain"
                )
            if request.analysis_date <= baseline.cutoff:
                raise InvalidResearchBaselineError(
                    "update cutoff must be strictly later than the current Research Chain head"
                )
            existing_exact = session.scalar(
                select(RunRecord)
                .where(
                    RunRecord.research_chain_id == chain.id,
                    RunRecord.baseline_revision_id == baseline.id,
                    RunRecord.request_json == request_json,
                )
                .order_by(RunRecord.created_at)
            )
            if existing_exact is not None:
                return self._view(existing_exact), False
            if chain.current_revision_id != baseline.id:
                raise InvalidResearchBaselineError(
                    "update must target the current Research Chain head"
                )
            active = session.scalar(
                select(RunRecord).where(
                    RunRecord.research_chain_id == chain.id,
                    RunRecord.status.in_((RunStatus.QUEUED.value, RunStatus.RUNNING.value)),
                )
            )
            if active is not None:
                raise InvalidResearchBaselineError(
                    "a queued or running update already exists for this Research Chain"
                )
            if idempotency_key:
                reused = session.scalar(
                    select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                )
                if reused is not None:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    )
            run_id = str(uuid4())
            record = RunRecord(
                id=run_id,
                idempotency_key=idempotency_key,
                status=RunStatus.QUEUED.value,
                research_chain_requested=False,
                update_intent_id=str(uuid4()),
                research_chain_id=chain.id,
                baseline_revision_id=baseline.id,
                research_execution_strategy=execution_strategy.value,
                request_json=request_json,
                config_json=_sanitize_payload(config_snapshot),
                version=__version__,
                current_attempt=1,
                cancel_requested=False,
                metrics_json=RunMetrics().model_dump(mode="json"),
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.add(
                RunAttemptRecord(
                    run_id=run_id,
                    attempt=1,
                    status=RunStatus.QUEUED.value,
                    checkpoint_thread_id=self.checkpoint_thread_id(run_id, 1),
                    metrics_json=RunMetrics().model_dump(mode="json"),
                )
            )
        return self.get_run(run_id), True

    def set_research_update_audit(self, run_id: str, audit: ResearchUpdateAudit) -> None:
        return self.run_store.set_research_update_audit(run_id, audit)

    @staticmethod
    def checkpoint_thread_id(run_id: str, attempt: int) -> str:
        return f"run:{run_id}:attempt:{attempt}"

    def get_run(self, run_id: str) -> RunView:
        return self.run_store.get_run(run_id)

    def list_runs(
        self,
        *,
        trash_state: RunTrashState = RunTrashState.ACTIVE,
        status: RunStatus | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> RunPage:
        return self.run_store.list_runs(
            trash_state=trash_state, status=status, q=q, limit=limit, offset=offset
        )

    def trash_runs(self, run_ids: tuple[str, ...]) -> tuple[tuple[RunView, ...], int]:
        return self.run_store.trash_runs(run_ids)

    def restore_runs(self, run_ids: tuple[str, ...]) -> tuple[tuple[RunView, ...], int]:
        return self.run_store.restore_runs(run_ids)

    def set_instrument_name(self, run_id: str, instrument_name: str | None) -> RunView:
        return self.run_store.set_instrument_name(run_id, instrument_name)

    def set_instrument_local_name(self, run_id: str, instrument_local_name: str | None) -> RunView:
        return self.run_store.set_instrument_local_name(run_id, instrument_local_name)

    def recent_instruments(self, *, limit: int = 20) -> tuple[RecentInstrument, ...]:
        return self.run_store.recent_instruments(limit=limit)

    def purge_expired_trash(self, *, cutoff: datetime, batch_size: int = 50) -> int:
        """Delete checkpoint and aggregate rows in one facade-owned transaction."""
        if cutoff.tzinfo is not None:
            cutoff = cutoff.astimezone(UTC).replace(tzinfo=None)
        batch_size = min(max(1, batch_size), 200)
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                run_ids = tuple(
                    connection.scalars(
                        select(RunRecord.id)
                        .where(
                            RunRecord.trashed_at.is_not(None),
                            RunRecord.trashed_at <= cutoff,
                        )
                        .order_by(RunRecord.trashed_at, RunRecord.id)
                        .limit(batch_size)
                    )
                )
                if not run_ids:
                    connection.commit()
                    return 0
                checkpoint_threads = tuple(
                    dict.fromkeys(
                        connection.scalars(
                            select(RunAttemptRecord.checkpoint_thread_id)
                            .where(RunAttemptRecord.run_id.in_(run_ids))
                            .order_by(RunAttemptRecord.id)
                        )
                    )
                )
                for checkpoint_thread in checkpoint_threads:
                    connection.exec_driver_sql(
                        "DELETE FROM writes WHERE thread_id = ?",
                        (checkpoint_thread,),
                    )
                    connection.exec_driver_sql(
                        "DELETE FROM checkpoints WHERE thread_id = ?",
                        (checkpoint_thread,),
                    )
                deleted = connection.execute(
                    delete(RunRecord).where(
                        RunRecord.id.in_(run_ids),
                        RunRecord.trashed_at.is_not(None),
                        RunRecord.trashed_at <= cutoff,
                    )
                ).rowcount
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return int(deleted or 0)

    def claim_next(self, worker_id: str, lease_seconds: int) -> RunView | None:
        return self.run_store.claim_next(worker_id, lease_seconds)

    def claim_run(self, run_id: str, worker_id: str, lease_seconds: int) -> RunView:
        return self.run_store.claim_run(run_id, worker_id, lease_seconds)

    def heartbeat(self, run_id: str, worker_id: str, lease_seconds: int) -> bool:
        return self.run_store.heartbeat(run_id, worker_id, lease_seconds)

    def release_claim(
        self, run_id: str, worker_id: str, *, metrics: RunMetrics | None = None
    ) -> RunView:
        return self.run_store.release_claim(run_id, worker_id, metrics=metrics)

    def request_cancel(self, run_id: str) -> RunView:
        return self.run_store.request_cancel(run_id)

    def cancel_requested(self, run_id: str) -> bool:
        return self.run_store.cancel_requested(run_id)

    def retry(self, run_id: str) -> RunView:
        return self.run_store.retry(run_id)

    def freeze_information_frontier(self, run_id: str, frontier: datetime) -> datetime:
        return self.run_store.freeze_information_frontier(run_id, frontier)

    def checkpoint_thread(self, run_id: str) -> str:
        return self.run_store.checkpoint_thread(run_id)

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        node: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        return self.run_store.append_event(run_id, event_type, node=node, payload=payload)

    def list_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[RunEvent]:
        return self.run_store.list_events(run_id, after_sequence=after_sequence, limit=limit)

    def list_recoveries(self, run_id: str) -> tuple[StructuredRecoveryNotice, ...]:
        return self.run_store.list_recoveries(run_id)

    def append_artifact(
        self, run_id: str, draft: ResearchArtifactDraft
    ) -> tuple[ResearchArtifact, RunEvent | None]:
        return self.run_store.append_artifact(run_id, draft)

    def list_artifacts(self, run_id: str, *, attempt: int | None = None) -> list[ResearchArtifact]:
        return self.run_store.list_artifacts(run_id, attempt=attempt)

    def seal_evidence(
        self, run_id: str, bundle: EvidenceBundle
    ) -> tuple[EvidenceSealView, RunEvent | None]:
        return self.run_store.seal_evidence(run_id, bundle)

    def evidence_status(self, run_id: str) -> EvidenceSealView:
        return self.run_store.evidence_status(run_id)

    def get_evidence(self, run_id: str) -> EvidenceBundle:
        return self.run_store.get_evidence(run_id)

    def complete(
        self,
        run_id: str,
        result: AnalysisResult,
        *,
        evidence: EvidenceBundle,
        benchmark: str,
        revision_draft: ResearchRevisionDraft | None = None,
    ) -> RunMetrics:
        now = _utc_naive()
        with self.sessions.begin() as session:
            if revision_draft is not None:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value:
                raise InvalidRunTransitionError(record.status)
            sealed = session.get(RunEvidenceRecord, run_id)
            if sealed is None:
                raise EvidenceNotSealedError(run_id)
            if sealed.digest != evidence.digest:
                raise EvidenceConflictError("completed result does not match the sealed evidence")
            if result.decision is not None:
                request = AnalysisRequest.model_validate(record.request_json)
                market = self.market_bucket(request.ticker)
                decision = DecisionRecord(
                    run_id=run_id,
                    ticker=request.ticker,
                    market=market,
                    asset_type=request.asset_type.value,
                    analysis_date=request.analysis_date,
                    rating=result.decision.rating.value,
                    confidence=result.decision.confidence,
                    decision_json=result.decision.model_dump(mode="json"),
                    numeric_audit_json=(
                        result.numeric_audit.model_dump(mode="json")
                        if result.numeric_audit is not None
                        else None
                    ),
                    created_at=now,
                )
                session.add(decision)
                session.flush()
                outcome = OutcomeRecord(
                    decision_id=decision.id,
                    status=OutcomeObservationStatus.PENDING.value,
                    benchmark=benchmark,
                    market_timezone=str(market_timezone(request.ticker)),
                    method_category=METHOD_CATEGORY,
                    method_version=METHOD_VERSION,
                    price_semantics=PRICE_SEMANTICS,
                    adjustment_semantics=ADJUSTMENT_SEMANTICS,
                    horizon_limit=HORIZON_LIMIT,
                    limitations_json=list(OBSERVATION_LIMITATIONS),
                    holding_intervals=5,
                    next_check_at=max(
                        now,
                        earliest_outcome_check_at(
                            ticker=request.ticker,
                            analysis_date=request.analysis_date,
                            holding_intervals=5,
                        ).replace(tzinfo=None),
                    ),
                )
                session.add(outcome)
            record.status = RunStatus.SUCCEEDED.value
            record.finished_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.SUCCEEDED.value
            attempt.finished_at = now
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            aggregate = self._merge_metrics(record, attempt, result.metrics)
            if record.research_chain_requested:
                if revision_draft is None:
                    raise ValueError("explicit Research Chain execution requires a Revision draft")
                revision_id = self._create_initial_revision(
                    session,
                    record=record,
                    draft=revision_draft,
                    metrics=aggregate,
                    created_at=now,
                )
                session.flush()
                if result.decision is not None:
                    outcome.research_revision_id = revision_id
            elif record.research_chain_id is not None:
                if revision_draft is None:
                    raise ValueError("Research Chain update requires a Revision draft")
                revision_id = self._advance_research_chain(
                    session,
                    record=record,
                    draft=revision_draft,
                    metrics=aggregate,
                    created_at=now,
                )
                session.flush()
                if result.decision is not None:
                    outcome.research_revision_id = revision_id
            elif revision_draft is not None:
                raise ValueError("ordinary runs cannot create a Research Revision")
        return aggregate

    def fail(
        self, run_id: str, error: BaseException, *, metrics: RunMetrics | None = None
    ) -> RunMetrics:
        return self.run_store.fail(run_id, error, metrics=metrics)

    def finish_cancel(self, run_id: str, *, metrics: RunMetrics | None = None) -> RunMetrics:
        return self.run_store.finish_cancel(run_id, metrics=metrics)

    def get_result(self, run_id: str) -> AnalysisResult:
        return self.run_store.get_result(run_id)

    def list_attempts(self, run_id: str) -> tuple[RunAttemptView, ...]:
        return self.run_store.list_attempts(run_id)

    def _run_exists(self, run_id: str) -> bool:
        with self.engine.connect() as connection:
            return (
                connection.execute(select(RunRecord.id).where(RunRecord.id == run_id)).first()
                is not None
            )

    @staticmethod
    def _artifact(record: Any) -> ResearchArtifact:
        content_models = {
            "analyst_report": AnalystReport,
            "decision_brief": DecisionBrief,
            "research_case": ResearchCase,
            "debate_agenda": DebateAgenda,
            "rebuttal_review": RebuttalReview,
            "judge_draft": JudgeDraft,
            "risk_review": RiskReview,
            "research_decision": ResearchDecision,
        }
        model = content_models.get(record["content_type"])
        if model is None:
            raise ValueError(f"unsupported research artifact type: {record['content_type']}")
        generation_method = ArtifactGenerationMethod(record["generation_method"])
        generation_observations = tuple(
            ArtifactGenerationObservation.model_validate(item)
            for item in (record["generation_observations_json"] or ())
        )
        content = model.model_validate(record["content_json"])
        return ResearchArtifact(
            id=record["id"],
            run_id=record["run_id"],
            attempt=record["attempt"],
            stage=record["stage"],
            role=record["role"],
            round=record["round"],
            schema_version=record["schema_version"],
            prompt_version=record["prompt_version"],
            generation_method=generation_method,
            generation_observations=generation_observations,
            content=content,
            created_at=_aware(record["created_at"]),
        )

    @staticmethod
    def _evidence_view(record: Any) -> EvidenceSealView:
        return EvidenceSealView(
            status="sealed",
            digest=record["digest"],
            item_count=record["item_count"],
            table_count=record["table_count"],
            sealed_attempt=record["sealed_attempt"],
            sealed_at=_aware(record["sealed_at"]),
        )

    def pending_outcomes(
        self, limit: int = 20, *, due_at: datetime | None = None
    ) -> list[dict[str, Any]]:
        return self.outcome_store.pending_outcomes(limit, due_at=due_at)

    def mark_outcome_checked(
        self,
        outcome_id: int,
        *,
        checked_at: datetime,
        next_check_at: datetime,
        error_message: str | None = None,
    ) -> None:
        return self.outcome_store.mark_outcome_checked(
            outcome_id,
            checked_at=checked_at,
            next_check_at=next_check_at,
            error_message=error_message,
        )

    def resolve_outcome(
        self,
        outcome_id: int,
        *,
        observation_start,
        observation_end,
        raw_return: float,
        alpha_return: float,
        reflection: str,
    ) -> None:
        return self.outcome_store.resolve_outcome(
            outcome_id,
            observation_start=observation_start,
            observation_end=observation_end,
            raw_return=raw_return,
            alpha_return=alpha_return,
            reflection=reflection,
        )

    def persist_outcome_observation(
        self, outcome_id: int, *, observation: Any, observed_at: datetime
    ) -> None:
        return self.outcome_store.persist_outcome_observation(
            outcome_id, observation=observation, observed_at=observed_at
        )

    def start_outcome_reflection_attempt(
        self,
        outcome_id: int,
        *,
        started_at: datetime,
        trigger: str = "outcome_settlement",
        origin: str = "automatic",
        attempt_kind: str = "initial",
    ) -> dict[str, int | str] | None:
        return self.outcome_store.start_outcome_reflection_attempt(
            outcome_id,
            started_at=started_at,
            trigger=trigger,
            origin=origin,
            attempt_kind=attempt_kind,
        )

    def start_outcome_reflection_repair_attempt(
        self, outcome_id: int, *, attempt_ids: dict[str, int | str], started_at: datetime
    ) -> dict[str, int | str]:
        return self.outcome_store.start_outcome_reflection_repair_attempt(
            outcome_id, attempt_ids=attempt_ids, started_at=started_at
        )

    @staticmethod
    def _finish_reflection_attempt(
        session: Session,
        *,
        reflection: ReflectionRecord,
        outcome_id: int,
        finished_at: datetime,
        attempt_ids: dict[str, int | str] | None,
        result: str,
        diagnostics: dict[str, str] | None = None,
        invalid_candidate: str | None = None,
        invalid_candidate_digest: str | None = None,
        invalid_candidate_length: int | None = None,
        validation_issues: list[str] | None = None,
        wall_time_seconds: float | None = None,
        schema_version: str = "outcome_reflection_legacy_unstructured.v1",
        finish_cycle: bool = True,
        usage: dict[str, int | float | None] | None = None,
    ) -> ReflectionAttemptRecord:
        if attempt_ids is None:
            cycle = ReflectionGenerationCycleRecord(
                id=str(uuid4()),
                outcome_id=outcome_id,
                status="running",
                trigger="repository_write",
                origin="automatic",
                retry_ordinal=0,
                queued_at=finished_at,
                started_at=finished_at,
            )
            session.add(cycle)
            session.flush()
            sequence = (
                session.scalar(
                    select(func.count())
                    .select_from(ReflectionAttemptRecord)
                    .where(ReflectionAttemptRecord.reflection_id == reflection.id)
                )
                or 0
            ) + 1
            attempt = ReflectionAttemptRecord(
                reflection_id=reflection.id,
                generation_cycle_id=cycle.id,
                sequence=sequence,
                trigger="repository_write",
                origin="automatic",
                attempt_kind="unstructured",
                started_at=finished_at,
                usage_status="not_reported",
                llm_calls=1,
            )
            session.add(attempt)
            reflection.current_generation_cycle_id = cycle.id
        else:
            attempt = session.get(ReflectionAttemptRecord, attempt_ids["attempt_id"])
            if attempt is None or attempt.reflection_id != reflection.id:
                raise ValueError("Reflection Attempt does not belong to Outcome")
            cycle = session.get(ReflectionGenerationCycleRecord, attempt_ids["cycle_id"])
            if cycle is None:
                raise ValueError("Reflection generation cycle is missing")
        candidate, digest, length = _invalid_candidate_audit(invalid_candidate)
        if invalid_candidate_digest is not None:
            digest = invalid_candidate_digest
        if invalid_candidate_length is not None:
            length = invalid_candidate_length
        attempt.finished_at = finished_at
        attempt.outcome = result
        attempt.candidate_schema_version = schema_version
        attempt.diagnostics_json = diagnostics
        attempt.wall_time_seconds = wall_time_seconds
        attempt.invalid_candidate = candidate
        attempt.invalid_candidate_digest = digest
        attempt.invalid_candidate_length = length
        attempt.validation_issues_json = validation_issues
        if usage and any(value is not None for value in usage.values()):
            attempt.usage_status = "reported"
            attempt.input_tokens = _usage_int(usage.get("input_tokens"))
            attempt.output_tokens = _usage_int(usage.get("output_tokens"))
            attempt.cache_hit_input_tokens = _usage_int(usage.get("cache_hit_input_tokens"))
            attempt.cache_miss_input_tokens = _usage_int(usage.get("cache_miss_input_tokens"))
            attempt.reasoning_output_tokens = _usage_int(usage.get("reasoning_output_tokens"))
            cost = usage.get("provider_reported_cost_usd")
            attempt.provider_reported_cost_usd = (
                float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None
            )
        if finish_cycle:
            cycle.status = {"generated": "succeeded", "invalid": "invalid"}.get(result, "failed")
            cycle.finished_at = finished_at
        return attempt

    def mark_reflection_failure(
        self,
        outcome_id: int,
        *,
        attempted_at: datetime,
        next_retry_at: datetime | None = None,
        error_code: str,
        attempt_ids: dict[str, int | str] | None = None,
        wall_time_seconds: float | None = None,
    ) -> None:
        return self.outcome_store.mark_reflection_failure(
            outcome_id,
            attempted_at=attempted_at,
            next_retry_at=next_retry_at,
            error_code=error_code,
            attempt_ids=attempt_ids,
            wall_time_seconds=wall_time_seconds,
        )

    def persist_generated_reflection(
        self,
        outcome_id: int,
        *,
        reflection: str | None = None,
        draft: OutcomeReflectionDraft | None = None,
        generated_at: datetime,
        allow_legacy_unstructured: bool = False,
        attempt_ids: dict[str, int | str] | None = None,
        wall_time_seconds: float | None = None,
        terminal_invalid: bool = True,
        validation_issues: list[str] | None = None,
        usage: dict[str, int | float | None] | None = None,
        invalid_candidate_digest: str | None = None,
        invalid_candidate_length: int | None = None,
    ) -> str | None:
        return self.outcome_store.persist_generated_reflection(
            outcome_id,
            reflection=reflection,
            draft=draft,
            generated_at=generated_at,
            allow_legacy_unstructured=allow_legacy_unstructured,
            attempt_ids=attempt_ids,
            wall_time_seconds=wall_time_seconds,
            terminal_invalid=terminal_invalid,
            validation_issues=validation_issues,
            usage=usage,
            invalid_candidate_digest=invalid_candidate_digest,
            invalid_candidate_length=invalid_candidate_length,
        )

    def enqueue_outcome_reflection_regeneration(
        self, outcome_id: int, *, idempotency_key: str, queued_at: datetime | None = None
    ) -> dict[str, Any]:
        return self.outcome_store.enqueue_outcome_reflection_regeneration(
            outcome_id, idempotency_key=idempotency_key, queued_at=queued_at
        )

    @staticmethod
    def _generation_cycle_view(cycle: ReflectionGenerationCycleRecord) -> dict[str, Any]:
        return {
            "id": cycle.id,
            "outcome_id": cycle.outcome_id,
            "status": cycle.status,
            "origin": cycle.origin,
            "trigger": cycle.trigger,
            "retry_ordinal": cycle.retry_ordinal,
            "queued_at": _aware(cycle.queued_at),
            "due_at": _aware(cycle.due_at),
        }

    def retry_outcome_reflection(self, outcome_id: int) -> bool:
        return self.outcome_store.retry_outcome_reflection(outcome_id)

    def retire_outcome_feedback(
        self, feedback_id: int, *, reason: OutcomeFeedbackRetirementReason, note: str | None
    ) -> dict[str, Any]:
        return self.outcome_store.retire_outcome_feedback(feedback_id, reason=reason, note=note)

    @staticmethod
    def _outcome_feedback_retirement_view(
        feedback: OutcomeFeedbackRecord,
    ) -> dict[str, Any]:
        return {
            "status": feedback.status,
            "retirement_reason": feedback.retirement_reason,
            "retirement_note": feedback.retirement_note,
            "retired_at": _aware(feedback.retired_at),
        }

    def memory_context(
        self, ticker: str, asset_type: str, *, same_limit: int = 5, cross_limit: int = 3
    ) -> MemoryContext:
        return self.outcome_store.memory_context(
            ticker, asset_type, same_limit=same_limit, cross_limit=cross_limit
        )

    def review_entries(
        self,
        *,
        outcome_id: int | None = None,
        ticker: str | None = None,
        market: str | None = None,
        q: str | None = None,
        status_group: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.outcome_store.review_entries(
            outcome_id=outcome_id,
            ticker=ticker,
            market=market,
            q=q,
            status_group=status_group,
            limit=limit,
        )

    def review_audit_detail(self, outcome_id: int) -> dict[str, Any] | None:
        return self.outcome_store.review_audit_detail(outcome_id)

    def backup(self, destination: Path) -> Path:
        destination = destination.expanduser().resolve()
        if destination == self.settings.database_path.resolve():
            raise ValueError("backup destination must differ from the live database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.settings.database_path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return destination

    def list_research_chains(self, *, instrument: str | None = None) -> tuple[ResearchChain, ...]:
        return self.research_store.list_research_chains(instrument=instrument)

    def get_research_chain(self, chain_id: str) -> ResearchChain:
        return self.research_store.get_research_chain(chain_id)

    def get_research_revision(self, revision_id: str) -> ResearchRevision:
        return self.research_store.get_research_revision(revision_id)

    @staticmethod
    def _create_initial_revision(
        session: Session,
        *,
        record: RunRecord,
        draft: ResearchRevisionDraft,
        metrics: RunMetrics,
        created_at: datetime,
    ) -> str:
        existing = session.scalar(
            select(ResearchRevisionRecord).where(
                ResearchRevisionRecord.producing_run_id == record.id
            )
        )
        if existing is not None:
            return existing.id
        request = AnalysisRequest.model_validate(record.request_json)
        has_primary = session.scalar(
            select(ResearchChainRecord.id).where(
                ResearchChainRecord.instrument == request.ticker,
                ResearchChainRecord.is_primary.is_(True),
            )
        )
        chain_id = str(uuid4())
        revision_id = str(uuid4())
        chain = ResearchChainRecord(
            id=chain_id,
            instrument=request.ticker,
            is_primary=has_primary is None,
            current_revision_id=None,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(chain)
        session.flush()
        session.add(
            RunRepository._revision_record(
                revision_id=revision_id,
                chain_id=chain_id,
                sequence=1,
                predecessor_revision_id=None,
                producing_run_id=record.id,
                draft=draft,
                metrics=metrics,
                created_at=created_at,
            )
        )
        chain.current_revision_id = revision_id
        return revision_id

    @staticmethod
    def _advance_research_chain(
        session: Session,
        *,
        record: RunRecord,
        draft: ResearchRevisionDraft,
        metrics: RunMetrics,
        created_at: datetime,
    ) -> str:
        chain = session.get(ResearchChainRecord, record.research_chain_id)
        baseline = session.get(ResearchRevisionRecord, record.baseline_revision_id)
        if (
            chain is None
            or baseline is None
            or baseline.chain_id != chain.id
            or chain.current_revision_id != baseline.id
        ):
            raise InvalidResearchBaselineError(
                "Update predecessor is no longer the current Research Chain head"
            )
        request = AnalysisRequest.model_validate(record.request_json)
        if request.ticker != chain.instrument or draft.current_state.instrument != chain.instrument:
            raise InvalidResearchBaselineError(
                "completed update Instrument does not match the Research Chain"
            )
        if draft.cutoff <= baseline.cutoff:
            raise InvalidResearchBaselineError(
                "completed update cutoff must be strictly later than the current Research Chain head"
            )
        revision_id = str(uuid4())
        session.add(
            RunRepository._revision_record(
                revision_id=revision_id,
                chain_id=chain.id,
                sequence=baseline.sequence + 1,
                predecessor_revision_id=baseline.id,
                producing_run_id=record.id,
                draft=draft,
                metrics=metrics,
                created_at=created_at,
            )
        )
        chain.current_revision_id = revision_id
        chain.updated_at = created_at
        return revision_id

    @staticmethod
    def _revision_record(
        *,
        revision_id: str,
        chain_id: str,
        sequence: int,
        predecessor_revision_id: str | None,
        producing_run_id: str,
        draft: ResearchRevisionDraft,
        metrics: RunMetrics,
        created_at: datetime,
    ) -> ResearchRevisionRecord:
        return ResearchRevisionRecord(
            id=revision_id,
            chain_id=chain_id,
            sequence=sequence,
            predecessor_revision_id=predecessor_revision_id,
            producing_run_id=producing_run_id,
            cutoff=draft.cutoff,
            information_frontier=(
                draft.information_frontier.isoformat()
                if draft.information_frontier is not None
                else None
            ),
            role=draft.role.value,
            execution_strategy=draft.execution_strategy.value,
            legacy_outcome=(
                "material_change"
                if draft.change_conclusion is None
                else "coverage_incomplete"
                if draft.change_conclusion is ResearchChangeConclusion.INDETERMINATE
                else draft.change_conclusion.value
            ),
            change_conclusion=(
                draft.change_conclusion.value if draft.change_conclusion is not None else None
            ),
            indeterminate_reason=(
                draft.indeterminate_reason.value if draft.indeterminate_reason is not None else None
            ),
            language=draft.current_state.language,
            current_state_json=draft.current_state.model_dump(mode="json"),
            delta_json=draft.delta.model_dump(mode="json"),
            coverage_json=draft.coverage.model_dump(mode="json"),
            update_summary_json=draft.update_summary.model_dump(mode="json"),
            evidence_snapshot_json=draft.evidence_snapshot.model_dump(mode="json"),
            research_update_audit_json=(
                draft.research_update_audit.model_dump(mode="json")
                if draft.research_update_audit is not None
                else None
            ),
            metrics_json=metrics.model_dump(mode="json"),
            created_at=created_at,
        )

    def _research_chain(
        self,
        session: Session,
        record: ResearchChainRecord,
    ) -> ResearchChain:
        revisions = tuple(
            self._research_revision(item)
            for item in session.scalars(
                select(ResearchRevisionRecord)
                .where(ResearchRevisionRecord.chain_id == record.id)
                .order_by(ResearchRevisionRecord.sequence)
            )
        )
        current = next(
            (item for item in revisions if item.id == record.current_revision_id),
            None,
        )
        if current is None:
            raise ValueError(f"Research Chain {record.id} has no current Revision")
        return ResearchChain(
            id=record.id,
            instrument=record.instrument,
            is_primary=record.is_primary,
            current_revision_id=current.id,
            current_revision=current,
            revisions=revisions,
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
        )

    @staticmethod
    def _research_revision(record: ResearchRevisionRecord) -> ResearchRevision:
        return ResearchRevision(
            id=record.id,
            chain_id=record.chain_id,
            sequence=record.sequence,
            predecessor_revision_id=record.predecessor_revision_id,
            producing_run_id=record.producing_run_id,
            cutoff=record.cutoff,
            information_frontier=(
                datetime.fromisoformat(record.information_frontier)
                if record.information_frontier is not None
                else None
            ),
            role=ResearchRevisionRole(record.role),
            execution_strategy=ResearchExecutionStrategy(record.execution_strategy),
            change_conclusion=(
                ResearchChangeConclusion(record.change_conclusion)
                if record.change_conclusion is not None
                else None
            ),
            indeterminate_reason=(
                IndeterminateReason(record.indeterminate_reason)
                if record.indeterminate_reason is not None
                else None
            ),
            current_state=CurrentResearchState.model_validate(record.current_state_json),
            delta=RevisionDelta.model_validate(record.delta_json),
            coverage=CoverageAttestation.model_validate(record.coverage_json),
            update_summary=UpdateSummary.model_validate(record.update_summary_json),
            evidence_snapshot=EffectiveEvidenceSnapshot.model_validate(
                record.evidence_snapshot_json
            ),
            research_update_audit=(
                ResearchUpdateAudit.model_validate(record.research_update_audit_json)
                if record.research_update_audit_json is not None
                else None
            ),
            metrics=RunMetrics.model_validate(record.metrics_json),
            created_at=_aware(record.created_at),
        )

    @staticmethod
    def market_bucket(ticker: str) -> str | None:
        try:
            return str(market_timezone(ticker))
        except ValueError:
            return None

    @staticmethod
    def _attempt(session: Session, record: RunRecord) -> RunAttemptRecord:
        return session.scalar(
            select(RunAttemptRecord).where(
                RunAttemptRecord.run_id == record.id,
                RunAttemptRecord.attempt == record.current_attempt,
            )
        )

    @staticmethod
    def _merge_metrics(
        record: RunRecord,
        attempt: RunAttemptRecord,
        segment: RunMetrics | None,
    ) -> RunMetrics:
        if segment is None:
            return RunMetrics.model_validate(record.metrics_json or {})
        attempt_metrics = merge_run_metrics(
            RunMetrics.model_validate(attempt.metrics_json or {}),
            segment,
        )
        aggregate = merge_run_metrics(
            RunMetrics.model_validate(record.metrics_json or {}),
            segment,
        )
        attempt.metrics_json = attempt_metrics.model_dump(mode="json")
        record.metrics_json = aggregate.model_dump(mode="json")
        return aggregate

    @staticmethod
    def _view(record: RunRecord) -> RunView:
        return RunView(
            id=record.id,
            source_run_id=record.source_run_id,
            instrument_name=record.instrument_name,
            instrument_local_name=record.instrument_local_name,
            research_chain_requested=record.research_chain_requested,
            update_intent_id=record.update_intent_id,
            research_chain_id=record.research_chain_id,
            baseline_revision_id=record.baseline_revision_id,
            research_execution_strategy=record.research_execution_strategy,
            research_update_audit=(
                ResearchUpdateAudit.model_validate(record.research_update_audit_json)
                if record.research_update_audit_json is not None
                else None
            ),
            information_frontier=(
                datetime.fromisoformat(record.information_frontier)
                if record.information_frontier is not None
                else None
            ),
            status=RunStatus(record.status),
            request=AnalysisRequest.model_validate(record.request_json),
            config_snapshot=record.config_json,
            attempt=record.current_attempt,
            cancel_requested=record.cancel_requested,
            error_code=record.error_code,
            error_message=record.error_message,
            metrics=RunMetrics.model_validate(record.metrics_json or {}),
            created_at=_aware(record.created_at),
            started_at=_aware(record.started_at),
            finished_at=_aware(record.finished_at),
            trashed_at=_aware(record.trashed_at),
            updated_at=_aware(record.updated_at),
        )

    @classmethod
    def _summary(
        cls,
        record: RunRecord,
        rating: str | None,
    ) -> RunSummaryView:
        return RunSummaryView(
            **cls._view(record).model_dump(),
            research_rating=ResearchRating(rating) if rating else None,
        )
