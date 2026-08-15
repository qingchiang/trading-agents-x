"""Internal persistence store; use RunRepository as the public facade."""

from __future__ import annotations

from ._repository_common import (
    _REFLECTION_RETRY_DELAYS,
    OUTCOME_REFLECTION_SCHEMA_VERSION,
    UTC,
    AnalysisRequest,
    Any,
    DecisionRecord,
    FeedbackSource,
    IntegrityError,
    MemoryContext,
    MemoryOutcome,
    MemoryRecord,
    ObservationQualificationInput,
    OutcomeFeedbackRecord,
    OutcomeFeedbackRetirementConflictError,
    OutcomeFeedbackRetirementNotFoundError,
    OutcomeFeedbackRetirementReason,
    OutcomeFeedbackStatus,
    OutcomeObservationStatus,
    OutcomeRecord,
    OutcomeReflectionDraft,
    OutcomeReflectionRegenerationConflictError,
    OutcomeReflectionRegenerationNotFoundError,
    OutcomeReflectionStatus,
    ReflectionAttemptRecord,
    ReflectionGenerationCycleRecord,
    ReflectionQualificationInput,
    ReflectionRecord,
    RepositoryStore,
    ResearchDecision,
    ResearchRevisionRecord,
    RunRecord,
    _aware,
    _sanitize_text,
    and_,
    datetime,
    derive_review_status,
    func,
    or_,
    reflection_candidate_lesson,
    review_status_in_group,
    select,
    uuid4,
)


class OutcomeReviewStore(RepositoryStore):
    def pending_outcomes(
        self,
        limit: int = 20,
        *,
        due_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        due = due_at or self._repository._now()
        if due.tzinfo is not None:
            due = due.astimezone(UTC).replace(tzinfo=None)
        stmt = (
            select(OutcomeRecord, DecisionRecord, ReflectionRecord)
            .join(DecisionRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
            .outerjoin(ReflectionRecord, ReflectionRecord.outcome_id == OutcomeRecord.id)
            .where(
                RunRecord.trashed_at.is_(None),
                or_(
                    and_(
                        OutcomeRecord.status == OutcomeObservationStatus.PENDING.value,
                        OutcomeRecord.next_check_at.is_not(None),
                        OutcomeRecord.next_check_at <= due,
                    ),
                    and_(
                        OutcomeRecord.status == OutcomeObservationStatus.RESOLVED.value,
                        or_(
                            ReflectionRecord.status == OutcomeReflectionStatus.PENDING.value,
                            and_(
                                ReflectionRecord.status
                                == OutcomeReflectionStatus.RETRYABLE_FAILURE.value,
                                ReflectionRecord.next_retry_at.is_not(None),
                                ReflectionRecord.next_retry_at <= due,
                            ),
                        ),
                    ),
                ),
            )
            .order_by(
                func.coalesce(OutcomeRecord.next_check_at, ReflectionRecord.next_retry_at),
                DecisionRecord.analysis_date,
            )
            .limit(limit)
        )
        with self.sessions() as session:
            return [
                {
                    "outcome_id": outcome.id,
                    "decision_id": decision.id,
                    "ticker": decision.ticker,
                    "analysis_date": decision.analysis_date,
                    "benchmark": outcome.benchmark,
                    "holding_intervals": outcome.holding_intervals,
                    "decision": decision.decision_json,
                    "status": outcome.status,
                    "observation_start": outcome.observation_start,
                    "observation_end": outcome.observation_end,
                    "raw_return": outcome.raw_return,
                    "alpha_return": outcome.alpha_return,
                    "market_timezone": outcome.market_timezone,
                    "reflection_status": reflection.status if reflection else None,
                    "next_check_at": _aware(
                        outcome.next_check_at
                        if outcome.status == OutcomeObservationStatus.PENDING.value
                        else reflection.next_retry_at
                        if reflection
                        else None
                    ),
                }
                for outcome, decision, reflection in session.execute(stmt)
            ]

    def mark_outcome_checked(
        self,
        outcome_id: int,
        *,
        checked_at: datetime,
        next_check_at: datetime,
        error_message: str | None = None,
    ) -> None:
        if checked_at.tzinfo is not None:
            checked_at = checked_at.astimezone(UTC).replace(tzinfo=None)
        if next_check_at.tzinfo is not None:
            next_check_at = next_check_at.astimezone(UTC).replace(tzinfo=None)
        with self.sessions.begin() as session:
            outcome = session.get(OutcomeRecord, outcome_id)
            if outcome is None:
                return
            outcome.last_checked_at = checked_at
            outcome.next_check_at = next_check_at
            outcome.error_message = _sanitize_text(error_message)

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
        now = _aware(self._repository._now())
        from .outcomes import OutcomeObservation

        self.persist_outcome_observation(
            outcome_id,
            observation=OutcomeObservation(
                raw_return=raw_return,
                alpha_return=alpha_return,
                holding_intervals=5,
                start_date=observation_start,
                end_date=observation_end,
            ),
            observed_at=now,
        )
        self.persist_generated_reflection(
            outcome_id,
            reflection=reflection,
            generated_at=now,
            allow_legacy_unstructured=True,
        )

    def persist_outcome_observation(
        self,
        outcome_id: int,
        *,
        observation: Any,
        observed_at: datetime,
    ) -> None:
        observed = observed_at
        if observed.tzinfo is not None:
            observed = observed.astimezone(UTC).replace(tzinfo=None)
        with self.sessions.begin() as session:
            outcome = session.scalar(
                select(OutcomeRecord)
                .join(
                    DecisionRecord,
                    OutcomeRecord.decision_id == DecisionRecord.id,
                )
                .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
                .where(
                    OutcomeRecord.id == outcome_id,
                    RunRecord.trashed_at.is_(None),
                )
            )
            if outcome is None or outcome.status == OutcomeObservationStatus.RESOLVED.value:
                return
            outcome.status = OutcomeObservationStatus.RESOLVED.value
            outcome.observation_start = observation.start_date
            outcome.observation_end = observation.end_date
            outcome.holding_intervals = observation.holding_intervals
            outcome.raw_return = observation.raw_return
            outcome.alpha_return = observation.alpha_return
            outcome.last_checked_at = observed
            outcome.next_check_at = None
            outcome.resolved_at = observed
            outcome.data_available_at = observed
            outcome.error_message = None
            session.add(
                ReflectionRecord(
                    outcome_id=outcome.id,
                    status=OutcomeReflectionStatus.PENDING.value,
                    text=None,
                    created_at=observed,
                )
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
        """Reserve the sole active generation cycle before invoking an LLM."""
        started = (
            started_at.astimezone(UTC).replace(tzinfo=None)
            if started_at.tzinfo is not None
            else started_at
        )
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            reflection = session.scalar(
                select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
            )
            if reflection is None or reflection.status == OutcomeReflectionStatus.GENERATED.value:
                return None
            existing = session.scalar(
                select(ReflectionGenerationCycleRecord).where(
                    ReflectionGenerationCycleRecord.outcome_id == outcome_id,
                    ReflectionGenerationCycleRecord.status.in_(("queued", "running")),
                )
            )
            if existing is not None:
                if existing.status != "queued" or (
                    existing.due_at is not None and existing.due_at > started
                ):
                    return None
                cycle = existing
                cycle.status = "running"
                cycle.started_at = started
            else:
                if reflection.status != OutcomeReflectionStatus.PENDING.value:
                    return None
                cycle = ReflectionGenerationCycleRecord(
                    id=str(uuid4()),
                    outcome_id=outcome_id,
                    status="running",
                    trigger=trigger,
                    origin=origin,
                    retry_ordinal=0,
                    queued_at=started,
                    due_at=started,
                    started_at=started,
                )
                try:
                    with session.begin_nested():
                        session.add(cycle)
                        session.flush()
                except IntegrityError:
                    return None
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
                trigger=cycle.trigger,
                origin=cycle.origin,
                attempt_kind=attempt_kind,
                started_at=started,
                usage_status="not_reported",
                llm_calls=1,
            )
            session.add(attempt)
            session.flush()
            reflection.current_generation_cycle_id = cycle.id
            reflection.last_attempted_at = started
            return {"cycle_id": cycle.id, "attempt_id": attempt.id}

    def start_outcome_reflection_repair_attempt(
        self,
        outcome_id: int,
        *,
        attempt_ids: dict[str, int | str],
        started_at: datetime,
    ) -> dict[str, int | str]:
        """Append the sole permitted schema repair to an active generation cycle."""
        started = (
            started_at.astimezone(UTC).replace(tzinfo=None)
            if started_at.tzinfo is not None
            else started_at
        )
        with self.sessions.begin() as session:
            reflection = session.scalar(
                select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
            )
            if reflection is None:
                raise ValueError("Outcome Reflection is missing")
            cycle = session.get(ReflectionGenerationCycleRecord, attempt_ids["cycle_id"])
            if cycle is None or cycle.outcome_id != outcome_id or cycle.status != "running":
                raise ValueError("Outcome Reflection generation cycle is not active")
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
                trigger=cycle.trigger,
                origin=cycle.origin,
                attempt_kind="repair",
                started_at=started,
                usage_status="not_reported",
                llm_calls=1,
            )
            session.add(attempt)
            reflection.last_attempted_at = started
            session.flush()
            return {"cycle_id": cycle.id, "attempt_id": attempt.id}

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
        attempted = (
            attempted_at.astimezone(UTC).replace(tzinfo=None)
            if attempted_at.tzinfo is not None
            else attempted_at
        )
        retry = (
            next_retry_at.astimezone(UTC).replace(tzinfo=None)
            if next_retry_at is not None and next_retry_at.tzinfo is not None
            else next_retry_at
        )
        with self.sessions.begin() as session:
            reflection = session.scalar(
                select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
            )
            if reflection is None or reflection.status == OutcomeReflectionStatus.GENERATED.value:
                return
            reflection.status = OutcomeReflectionStatus.RETRYABLE_FAILURE.value
            reflection.last_attempted_at = attempted
            reflection.error_code = _sanitize_text(error_code, limit=80)
            self._finish_reflection_attempt(
                session,
                reflection=reflection,
                outcome_id=outcome_id,
                finished_at=attempted,
                attempt_ids=attempt_ids,
                result="provider_failure",
                diagnostics={"error_code": reflection.error_code or "unknown"},
                wall_time_seconds=wall_time_seconds,
            )
            cycle = (
                session.get(ReflectionGenerationCycleRecord, attempt_ids["cycle_id"])
                if attempt_ids is not None
                else None
            )
            if (
                cycle is not None
                and cycle.origin == "automatic"
                and cycle.retry_ordinal < len(_REFLECTION_RETRY_DELAYS)
            ):
                retry = attempted + _REFLECTION_RETRY_DELAYS[cycle.retry_ordinal]
                scheduled = ReflectionGenerationCycleRecord(
                    id=str(uuid4()),
                    outcome_id=outcome_id,
                    status="queued",
                    trigger="outcome_settlement",
                    origin="automatic",
                    retry_ordinal=cycle.retry_ordinal + 1,
                    queued_at=attempted,
                    due_at=retry,
                )
                session.add(scheduled)
                reflection.current_generation_cycle_id = scheduled.id
                reflection.next_retry_at = retry
            else:
                reflection.next_retry_at = None

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
        generated = generated_at
        if generated.tzinfo is not None:
            generated = generated.astimezone(UTC).replace(tzinfo=None)
        raw_candidate = reflection if isinstance(reflection, str) else None
        text = draft.readable_text if draft is not None else (reflection or "").strip()
        with self.sessions.begin() as session:
            row = session.execute(
                select(
                    ReflectionRecord,
                    OutcomeRecord,
                    DecisionRecord,
                    ResearchRevisionRecord,
                )
                .join(OutcomeRecord, OutcomeRecord.id == ReflectionRecord.outcome_id)
                .join(DecisionRecord, DecisionRecord.id == OutcomeRecord.decision_id)
                .outerjoin(
                    ResearchRevisionRecord,
                    ResearchRevisionRecord.id == OutcomeRecord.research_revision_id,
                )
                .where(OutcomeRecord.id == outcome_id)
            ).first()
            if row is None:
                return None
            reflection_record, outcome, decision, revision = row
            if reflection_record.status == OutcomeReflectionStatus.GENERATED.value:
                return OutcomeReflectionStatus.GENERATED.value
            reflection_record.last_attempted_at = generated
            reflection_record.next_retry_at = None
            reflection_record.error_code = None
            structural_issues = list(validation_issues or ())
            if not text:
                structural_issues.append("empty_candidate")
            if len(text) > 12_000:
                structural_issues.append("candidate_too_long")
            if not allow_legacy_unstructured and draft is None:
                structural_issues.append("missing_structured_draft")
            if structural_issues:
                reflection_record.status = OutcomeReflectionStatus.INVALID.value
                reflection_record.text = None
                reflection_record.candidate_json = None
                self._finish_reflection_attempt(
                    session,
                    reflection=reflection_record,
                    outcome_id=outcome_id,
                    finished_at=generated,
                    attempt_ids=attempt_ids,
                    result="invalid",
                    invalid_candidate=raw_candidate,
                    invalid_candidate_digest=invalid_candidate_digest,
                    invalid_candidate_length=invalid_candidate_length,
                    validation_issues=list(dict.fromkeys(structural_issues)),
                    wall_time_seconds=wall_time_seconds,
                    schema_version=OUTCOME_REFLECTION_SCHEMA_VERSION,
                    finish_cycle=terminal_invalid,
                    usage=usage,
                )
                if not terminal_invalid:
                    reflection_record.status = OutcomeReflectionStatus.PENDING.value
                return OutcomeReflectionStatus.INVALID.value
            if (
                outcome.data_available_at is None
                or outcome.observation_start is None
                or outcome.observation_end is None
            ):
                raise ValueError("Outcome Observation is incomplete")
            qualification_started_at = max(
                self._repository._now(), outcome.data_available_at, generated
            )
            qualification = self._repository._qualify_reflection(
                source=FeedbackSource(
                    decision_id=decision.id,
                    revision_id=outcome.research_revision_id,
                    decision_rating=decision.rating,
                    decision_thesis=str(decision.decision_json.get("thesis") or ""),
                    decision_cutoff=decision.analysis_date,
                    revision_cutoff=revision.cutoff if revision is not None else None,
                    ticker=decision.ticker,
                    market=decision.market,
                ),
                observation=ObservationQualificationInput(
                    start=outcome.observation_start,
                    end=outcome.observation_end,
                    data_available_at=outcome.data_available_at,
                    method_category=outcome.method_category,
                    horizon_limit=outcome.horizon_limit,
                ),
                reflection=ReflectionQualificationInput(
                    method_lesson=(
                        draft.method_lesson
                        if draft is not None
                        else reflection_candidate_lesson(text) or ""
                    ),
                    generated_at=generated,
                ),
                qualified_at=qualification_started_at,
            )
            qualified_at = max(self._repository._now(), qualification_started_at)
            reflection_record.status = OutcomeReflectionStatus.GENERATED.value
            reflection_record.text = text
            reflection_record.candidate_json = (
                draft.audit_candidate() if draft is not None else qualification.candidate
            )
            reflection_record.generated_at = generated
            available_at = max(outcome.data_available_at, generated, qualified_at)
            session.add(
                OutcomeFeedbackRecord(
                    reflection_id=reflection_record.id,
                    status=qualification.status.value,
                    qualification_policy_version=(
                        "outcome_feedback_qualification.v1"
                        if allow_legacy_unstructured
                        else qualification.qualification_policy_version
                    ),
                    reasons_json=list(qualification.reasons),
                    method_category=outcome.method_category,
                    horizon_limit=outcome.horizon_limit,
                    applicability_json=qualification.applicability,
                    qualified_at=qualified_at,
                    available_at=available_at,
                )
            )
            attempt = self._finish_reflection_attempt(
                session,
                reflection=reflection_record,
                outcome_id=outcome_id,
                finished_at=generated,
                attempt_ids=attempt_ids,
                result="generated",
                wall_time_seconds=wall_time_seconds,
                schema_version=(
                    OUTCOME_REFLECTION_SCHEMA_VERSION
                    if draft is not None
                    else "outcome_reflection_legacy_unstructured.v1"
                ),
                usage=draft.usage if draft is not None else usage,
            )
            session.flush()
            reflection_record.successful_attempt_id = attempt.id
            return OutcomeReflectionStatus.GENERATED.value

    def enqueue_outcome_reflection_regeneration(
        self,
        outcome_id: int,
        *,
        idempotency_key: str,
        queued_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create or replay the one manual Reflection cycle allowed by the lifecycle."""
        key = idempotency_key.strip()
        if not key:
            raise OutcomeReflectionRegenerationConflictError("Idempotency-Key is required")
        if len(key) > 200:
            raise OutcomeReflectionRegenerationConflictError("Idempotency-Key is too long")
        queued = queued_at or _aware(self._repository._now())
        if queued.tzinfo is not None:
            queued = queued.astimezone(UTC).replace(tzinfo=None)
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.execute(
                select(OutcomeRecord, ReflectionRecord, OutcomeFeedbackRecord)
                .outerjoin(ReflectionRecord, ReflectionRecord.outcome_id == OutcomeRecord.id)
                .outerjoin(
                    OutcomeFeedbackRecord,
                    OutcomeFeedbackRecord.reflection_id == ReflectionRecord.id,
                )
                .where(OutcomeRecord.id == outcome_id)
            ).first()
            if row is None:
                raise OutcomeReflectionRegenerationNotFoundError(str(outcome_id))
            outcome, reflection, feedback = row
            review_status = derive_review_status(
                outcome_status=outcome.status,
                outcome_error=outcome.error_message,
                reflection_status=reflection.status if reflection else None,
                reflection_next_retry_at=reflection.next_retry_at if reflection else None,
                feedback_status=feedback.status if feedback else None,
            )
            existing_key = session.scalar(
                select(ReflectionGenerationCycleRecord).where(
                    ReflectionGenerationCycleRecord.outcome_id == outcome_id,
                    ReflectionGenerationCycleRecord.idempotency_key == key,
                )
            )
            if existing_key is not None:
                return {
                    "cycle": self._generation_cycle_view(existing_key),
                    "review_status": review_status,
                    "reflection_status": reflection.status if reflection else None,
                }
            if review_status == "lifecycle_inconsistent":
                raise OutcomeReflectionRegenerationConflictError("Review lifecycle is inconsistent")
            active = session.scalar(
                select(ReflectionGenerationCycleRecord).where(
                    ReflectionGenerationCycleRecord.outcome_id == outcome_id,
                    ReflectionGenerationCycleRecord.status.in_(("queued", "running")),
                )
            )
            if active is not None:
                raise OutcomeReflectionRegenerationConflictError(
                    "Outcome Reflection generation is already active",
                    active_cycle_id=active.id,
                )
            if (
                outcome.status != OutcomeObservationStatus.RESOLVED.value
                or reflection is None
                or reflection.status == OutcomeReflectionStatus.GENERATED.value
                or feedback is not None
            ):
                raise OutcomeReflectionRegenerationConflictError(
                    "Outcome Reflection cannot be regenerated from this lifecycle state"
                )
            retry_exhausted = (
                reflection.status == OutcomeReflectionStatus.RETRYABLE_FAILURE.value
                and reflection.next_retry_at is None
            )
            if reflection.status != OutcomeReflectionStatus.INVALID.value and not retry_exhausted:
                raise OutcomeReflectionRegenerationConflictError(
                    "Outcome Reflection is not eligible for regeneration"
                )
            cycle = ReflectionGenerationCycleRecord(
                id=str(uuid4()),
                outcome_id=outcome_id,
                status="queued",
                trigger="user_regeneration",
                origin="manual",
                retry_ordinal=0,
                idempotency_key=key,
                queued_at=queued,
                due_at=queued,
            )
            session.add(cycle)
            reflection.status = OutcomeReflectionStatus.PENDING.value
            reflection.next_retry_at = None
            reflection.error_code = None
            reflection.current_generation_cycle_id = cycle.id
            session.flush()
            return {
                "cycle": self._generation_cycle_view(cycle),
                "review_status": derive_review_status(
                    outcome_status=outcome.status,
                    outcome_error=outcome.error_message,
                    reflection_status=reflection.status,
                    reflection_next_retry_at=reflection.next_retry_at,
                    feedback_status=feedback.status if feedback else None,
                ),
                "reflection_status": reflection.status,
            }

    def retry_outcome_reflection(self, outcome_id: int) -> bool:
        """Deprecated compatibility adapter for one release cycle."""
        try:
            self.enqueue_outcome_reflection_regeneration(
                outcome_id,
                idempotency_key=f"legacy-reflection-retry:{outcome_id}",
            )
        except OutcomeReflectionRegenerationNotFoundError:
            return True
        except OutcomeReflectionRegenerationConflictError:
            return False
        return True

    def retire_outcome_feedback(
        self,
        feedback_id: int,
        *,
        reason: OutcomeFeedbackRetirementReason,
        note: str | None,
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.execute(
                select(OutcomeRecord, ReflectionRecord, OutcomeFeedbackRecord)
                .join(ReflectionRecord, ReflectionRecord.outcome_id == OutcomeRecord.id)
                .join(
                    OutcomeFeedbackRecord,
                    OutcomeFeedbackRecord.reflection_id == ReflectionRecord.id,
                )
                .where(OutcomeFeedbackRecord.id == feedback_id)
            ).first()
            if row is None:
                raise OutcomeFeedbackRetirementNotFoundError(str(feedback_id))
            outcome, reflection, feedback = row
            if (
                derive_review_status(
                    outcome_status=outcome.status,
                    outcome_error=outcome.error_message,
                    reflection_status=reflection.status,
                    reflection_next_retry_at=reflection.next_retry_at,
                    feedback_status=feedback.status,
                )
                == "lifecycle_inconsistent"
            ):
                raise OutcomeFeedbackRetirementConflictError("Review lifecycle is inconsistent")
            if feedback.status == OutcomeFeedbackStatus.RETIRED.value:
                return {
                    **self._outcome_feedback_retirement_view(feedback),
                    "review_status": derive_review_status(
                        outcome_status=outcome.status,
                        outcome_error=outcome.error_message,
                        reflection_status=reflection.status,
                        reflection_next_retry_at=reflection.next_retry_at,
                        feedback_status=feedback.status,
                    ),
                }
            if feedback.status != OutcomeFeedbackStatus.ELIGIBLE.value:
                raise OutcomeFeedbackRetirementConflictError(
                    "Outcome Feedback is not eligible for retirement"
                )
            feedback.status = OutcomeFeedbackStatus.RETIRED.value
            feedback.retirement_reason = reason.value
            feedback.retirement_note = note
            feedback.retired_at = self._repository._now()
            session.flush()
            return {
                **self._outcome_feedback_retirement_view(feedback),
                "review_status": derive_review_status(
                    outcome_status=outcome.status,
                    outcome_error=outcome.error_message,
                    reflection_status=reflection.status,
                    reflection_next_retry_at=reflection.next_retry_at,
                    feedback_status=feedback.status,
                ),
            }

    def memory_context(
        self,
        ticker: str,
        asset_type: str,
        *,
        same_limit: int = 5,
        cross_limit: int = 3,
    ) -> MemoryContext:
        market = self.market_bucket(ticker)
        resolved = (
            select(
                DecisionRecord,
                OutcomeRecord,
                ReflectionRecord,
            )
            .join(OutcomeRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .join(
                ReflectionRecord,
                ReflectionRecord.outcome_id == OutcomeRecord.id,
            )
            .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
            .where(
                RunRecord.trashed_at.is_(None),
                OutcomeRecord.status == OutcomeObservationStatus.RESOLVED.value,
                OutcomeRecord.holding_intervals >= 5,
                OutcomeRecord.raw_return.is_not(None),
                OutcomeRecord.alpha_return.is_not(None),
                ReflectionRecord.status == OutcomeReflectionStatus.GENERATED.value,
                ReflectionRecord.text.is_not(None),
            )
            .order_by(
                OutcomeRecord.resolved_at.desc(),
                OutcomeRecord.id.desc(),
            )
        )
        with self.sessions() as session:
            rows = list(session.execute(resolved))
        same: list[MemoryRecord] = []
        cross: list[MemoryRecord] = []
        ticker_key = ticker.casefold()
        asset_type_key = asset_type.casefold()
        for decision_record, outcome_record, reflection_record in rows:
            reflection = (reflection_record.text or "").strip()
            if not reflection:
                continue
            try:
                decision = ResearchDecision.model_validate(decision_record.decision_json)
                outcome = MemoryOutcome(
                    benchmark=outcome_record.benchmark,
                    observation_start=outcome_record.observation_start,
                    observation_end=outcome_record.observation_end,
                    holding_intervals=outcome_record.holding_intervals,
                    raw_return=outcome_record.raw_return,
                    alpha_return=outcome_record.alpha_return,
                )
            except ValueError:
                continue
            if decision_record.ticker.casefold() == ticker_key and len(same) < max(0, same_limit):
                same.append(
                    MemoryRecord(
                        ref=f"memory:{decision_record.run_id}",
                        run_id=decision_record.run_id,
                        scope="same_ticker",
                        ticker=decision_record.ticker,
                        market=decision_record.market,
                        analysis_date=decision_record.analysis_date,
                        decision=decision,
                        outcome=outcome,
                        reflection=reflection,
                    )
                )
            elif (
                decision_record.ticker.casefold() != ticker_key
                and decision_record.asset_type.casefold() == asset_type_key
                and market is not None
                and decision_record.market == market
                and len(cross) < max(0, cross_limit)
            ):
                cross.append(
                    MemoryRecord(
                        ref=f"memory:{decision_record.run_id}",
                        run_id=decision_record.run_id,
                        scope="same_market",
                        ticker=decision_record.ticker,
                        market=decision_record.market,
                        analysis_date=decision_record.analysis_date,
                        reflection=reflection,
                    )
                )
        return MemoryContext(
            instrument=ticker,
            market=market,
            items=(*same, *cross),
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
        bounded_limit = min(max(1, limit), 500)
        filters_by_derived_status = status_group not in {None, "", "all"}
        stmt = (
            select(
                DecisionRecord,
                OutcomeRecord,
                ReflectionRecord,
                OutcomeFeedbackRecord,
                RunRecord.instrument_name,
                RunRecord.instrument_local_name,
                RunRecord.request_json,
            )
            .join(OutcomeRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
            .outerjoin(
                ReflectionRecord,
                ReflectionRecord.outcome_id == OutcomeRecord.id,
            )
            .outerjoin(
                OutcomeFeedbackRecord,
                OutcomeFeedbackRecord.reflection_id == ReflectionRecord.id,
            )
            .order_by(
                DecisionRecord.created_at.desc(),
                DecisionRecord.id.desc(),
            )
            .where(RunRecord.trashed_at.is_(None))
        )
        if not filters_by_derived_status:
            stmt = stmt.limit(bounded_limit)
        if outcome_id is not None:
            stmt = stmt.where(OutcomeRecord.id == outcome_id)
        if ticker and (ticker_query := ticker.strip().casefold()):
            stmt = stmt.where(
                func.lower(DecisionRecord.ticker).contains(
                    ticker_query,
                    autoescape=True,
                )
            )
        if market and (market_query := market.strip().casefold()):
            stmt = stmt.where(
                func.lower(func.coalesce(DecisionRecord.market, "")).contains(
                    market_query,
                    autoescape=True,
                )
            )
        if q and (query := q.strip().casefold()):
            decision_fields = (
                "$.rating",
                "$.thesis",
                "$.catalysts",
                "$.risks",
                "$.invalidation_conditions",
                "$.time_horizon",
                "$.scenarios",
                "$.unresolved_questions",
            )
            stmt = stmt.where(
                or_(
                    func.lower(DecisionRecord.run_id).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(DecisionRecord.ticker).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(func.coalesce(RunRecord.instrument_name, "")).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(func.coalesce(RunRecord.instrument_local_name, "")).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(func.coalesce(DecisionRecord.market, "")).contains(
                        query,
                        autoescape=True,
                    ),
                    *(
                        func.lower(
                            func.coalesce(
                                func.json_extract(
                                    DecisionRecord.decision_json,
                                    path,
                                ),
                                "",
                            )
                        ).contains(
                            query,
                            autoescape=True,
                        )
                        for path in decision_fields
                    ),
                    func.lower(func.coalesce(ReflectionRecord.text, "")).contains(
                        query,
                        autoescape=True,
                    ),
                )
            )
        with self.sessions() as session:
            reviews = []
            for (
                decision,
                outcome,
                reflection,
                feedback,
                instrument_name,
                instrument_local_name,
                request_json,
            ) in session.execute(stmt):
                review_status = derive_review_status(
                    outcome_status=outcome.status,
                    outcome_error=outcome.error_message,
                    reflection_status=reflection.status if reflection else None,
                    reflection_next_retry_at=(reflection.next_retry_at if reflection else None),
                    feedback_status=feedback.status if feedback else None,
                )
                if not review_status_in_group(review_status, status_group):
                    continue
                generation_cycle = (
                    session.get(
                        ReflectionGenerationCycleRecord,
                        reflection.current_generation_cycle_id,
                    )
                    if reflection and reflection.current_generation_cycle_id
                    else None
                )
                reviews.append(
                    {
                        "run_id": decision.run_id,
                        "outcome_id": outcome.id,
                        "review_status": review_status,
                        "lifecycle_actions_allowed": review_status != "lifecycle_inconsistent",
                        "ticker": decision.ticker,
                        "instrument_name": instrument_name,
                        "instrument_local_name": instrument_local_name,
                        "market": decision.market,
                        "asset_type": decision.asset_type,
                        "analysis_date": decision.analysis_date.isoformat(),
                        "profile": AnalysisRequest.model_validate(request_json).profile,
                        "decision": decision.decision_json,
                        "outcome": {
                            "status": outcome.status,
                            "source_decision_id": decision.id,
                            "source_revision_id": outcome.research_revision_id,
                            "benchmark": outcome.benchmark,
                            "market_timezone": outcome.market_timezone,
                            "method_category": outcome.method_category,
                            "method_version": outcome.method_version,
                            "price_semantics": outcome.price_semantics,
                            "adjustment_semantics": outcome.adjustment_semantics,
                            "horizon_limit": outcome.horizon_limit,
                            "limitations": outcome.limitations_json,
                            "observation_start": (
                                outcome.observation_start.isoformat()
                                if outcome.observation_start
                                else None
                            ),
                            "observation_end": (
                                outcome.observation_end.isoformat()
                                if outcome.observation_end
                                else None
                            ),
                            "holding_intervals": outcome.holding_intervals,
                            "raw_return": outcome.raw_return,
                            "alpha_return": outcome.alpha_return,
                            "resolved_at": _aware(outcome.resolved_at),
                            "data_available_at": _aware(outcome.data_available_at),
                            "last_checked_at": _aware(outcome.last_checked_at),
                            "next_check_at": _aware(outcome.next_check_at),
                            "error_message": outcome.error_message,
                        },
                        "method_feedback": (
                            reflection_candidate_lesson(
                                reflection.text or "", reflection.candidate_json
                            )
                            if review_status == "feedback_available" and reflection
                            else None
                        ),
                        "outcome_reflection": (
                            {
                                "status": reflection.status,
                                "created_at": _aware(reflection.created_at),
                                "generated_at": _aware(reflection.generated_at),
                                "last_attempted_at": _aware(reflection.last_attempted_at),
                                "next_retry_at": _aware(reflection.next_retry_at),
                                "error_code": reflection.error_code,
                                "generation_cycle": (
                                    self._generation_cycle_view(generation_cycle)
                                    if generation_cycle is not None
                                    else None
                                ),
                            }
                            if reflection
                            else None
                        ),
                        "outcome_feedback": (
                            {
                                "id": feedback.id,
                                "status": feedback.status,
                                "qualification_policy_version": (
                                    feedback.qualification_policy_version
                                ),
                                "reasons": feedback.reasons_json,
                                "method_category": feedback.method_category,
                                "horizon_limit": feedback.horizon_limit,
                                "applicability": feedback.applicability_json,
                                "qualified_at": _aware(feedback.qualified_at),
                                "available_at": _aware(feedback.available_at),
                                "retirement_reason": feedback.retirement_reason,
                                "retirement_note": feedback.retirement_note,
                                "retired_at": _aware(feedback.retired_at),
                            }
                            if feedback
                            else None
                        ),
                    }
                )
                if filters_by_derived_status and len(reviews) >= bounded_limit:
                    break
            return reviews

    def review_audit_detail(self, outcome_id: int) -> dict[str, Any] | None:
        """Return progressively disclosed audit-only Reflection provenance."""
        review = next(
            (
                item
                for item in self.review_entries(outcome_id=outcome_id, limit=1)
                if item["outcome_id"] == outcome_id
            ),
            None,
        )
        if review is None:
            return None
        with self.sessions() as session:
            reflection = session.scalar(
                select(ReflectionRecord)
                .join(OutcomeRecord, OutcomeRecord.id == ReflectionRecord.outcome_id)
                .join(DecisionRecord, DecisionRecord.id == OutcomeRecord.decision_id)
                .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
                .where(
                    OutcomeRecord.id == outcome_id,
                    RunRecord.trashed_at.is_(None),
                )
            )
            attempts = (
                list(
                    session.scalars(
                        select(ReflectionAttemptRecord)
                        .where(ReflectionAttemptRecord.reflection_id == reflection.id)
                        .order_by(ReflectionAttemptRecord.sequence, ReflectionAttemptRecord.id)
                    )
                )
                if reflection
                else []
            )
        metric_names = (
            "llm_calls",
            "input_tokens",
            "output_tokens",
            "cache_hit_input_tokens",
            "cache_miss_input_tokens",
            "reasoning_output_tokens",
            "wall_time_seconds",
            "provider_reported_cost_usd",
        )
        aggregate = {
            name: (
                sum(getattr(attempt, name) for attempt in attempts)
                if attempts and all(getattr(attempt, name) is not None for attempt in attempts)
                else None
            )
            for name in metric_names
        }
        usage_status = (
            "legacy_unknown"
            if any(attempt.usage_status == "legacy_unknown" for attempt in attempts)
            else "not_reported"
            if any(attempt.usage_status == "not_reported" for attempt in attempts)
            else "reported"
            if attempts
            else "not_reported"
        )
        return {
            "review": review,
            "reflection": reflection.text if reflection else None,
            "attempts": [
                {
                    "id": attempt.id,
                    "generation_cycle_id": attempt.generation_cycle_id,
                    "sequence": attempt.sequence,
                    "trigger": attempt.trigger,
                    "origin": attempt.origin,
                    "attempt_kind": attempt.attempt_kind,
                    "started_at": _aware(attempt.started_at),
                    "finished_at": _aware(attempt.finished_at),
                    "outcome": attempt.outcome,
                    "attempt_schema_version": attempt.attempt_schema_version,
                    "candidate_schema_version": attempt.candidate_schema_version,
                    "diagnostics": attempt.diagnostics_json,
                    "usage": {
                        name: getattr(attempt, name) for name in ("usage_status", *metric_names)
                    },
                    "invalid_candidate": attempt.invalid_candidate,
                    "invalid_candidate_digest": attempt.invalid_candidate_digest,
                    "invalid_candidate_length": attempt.invalid_candidate_length,
                    "validation_issues": attempt.validation_issues_json,
                }
                for attempt in attempts
            ],
            "aggregate_usage": {
                "usage_status": usage_status,
                "attempt_count": len(attempts),
                **aggregate,
            },
        }
