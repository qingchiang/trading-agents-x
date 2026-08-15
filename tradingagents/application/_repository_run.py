"""Internal persistence store; use RunRepository as the public facade."""

from __future__ import annotations

from ._repository_common import (
    _TERMINAL_STATUSES,
    AnalysisRequest,
    AnalysisResult,
    AnalystReport,
    Any,
    ArtifactConflictError,
    DecisionNumericAuditAppendix,
    DecisionRecord,
    EvidenceBundle,
    EvidenceConflictError,
    EvidenceNotSealedError,
    EvidenceSealView,
    IdempotencyConflictError,
    IntegrityError,
    InvalidRunTransitionError,
    NumericAuditStatus,
    RecentInstrument,
    RepositoryStore,
    ResearchArtifact,
    ResearchArtifactDraft,
    ResearchDecision,
    ResearchUpdateAudit,
    ResearchWarning,
    RunArtifactRecord,
    RunAttemptRecord,
    RunAttemptView,
    RunEvent,
    RunEventRecord,
    RunEvidenceRecord,
    RunMetrics,
    RunNotFoundError,
    RunPage,
    RunRecord,
    RunStatus,
    RunTrashState,
    RunView,
    StructuredRecoveryNotice,
    __version__,
    _aware,
    _numeric_audit_warning_message,
    _sanitize_payload,
    _sanitize_text,
    _utc_naive,
    and_,
    datetime,
    func,
    or_,
    order_reports,
    rebuild_structured_recoveries,
    select,
    timedelta,
    update,
    uuid4,
)


class RunEventEvidenceStore(RepositoryStore):
    def create_run(
        self,
        request: AnalysisRequest,
        config_snapshot: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        source_run_id: str | None = None,
        research_chain_requested: bool = False,
    ) -> tuple[RunView, bool]:
        now = _utc_naive()
        request_json = request.model_dump(mode="json")
        try:
            with self.sessions.begin() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                if idempotency_key:
                    existing = session.scalar(
                        select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                    )
                    if existing is not None:
                        if (
                            existing.request_json != request_json
                            or existing.source_run_id != source_run_id
                            or existing.research_chain_requested != research_chain_requested
                        ):
                            raise IdempotencyConflictError(
                                "idempotency key was already used for a different request"
                            )
                        return self._view(existing), False
                if source_run_id is not None:
                    source = session.get(RunRecord, source_run_id)
                    if source is None:
                        raise RunNotFoundError(source_run_id)
                    if source.status not in _TERMINAL_STATUSES:
                        raise InvalidRunTransitionError(
                            "source run must be terminal before it can be "
                            "used as a research template"
                        )
                run_id = str(uuid4())
                record = RunRecord(
                    id=run_id,
                    source_run_id=source_run_id,
                    idempotency_key=idempotency_key,
                    research_chain_requested=research_chain_requested,
                    status=RunStatus.QUEUED.value,
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
        except IntegrityError as exc:
            if idempotency_key is None:
                raise
            with self.sessions() as session:
                existing = session.scalar(
                    select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                )
                if existing is None:
                    raise
                if (
                    existing.request_json != request_json
                    or existing.source_run_id != source_run_id
                    or existing.research_chain_requested != research_chain_requested
                ):
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    ) from exc
                return self._view(existing), False
        return self.get_run(run_id), True

    def set_research_update_audit(
        self,
        run_id: str,
        audit: ResearchUpdateAudit,
    ) -> None:
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value:
                raise InvalidRunTransitionError(record.status)
            record.research_update_audit_json = audit.model_dump(mode="json")
            record.updated_at = _utc_naive()

    def get_run(self, run_id: str) -> RunView:
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            return self._view(record)

    def list_runs(
        self,
        *,
        trash_state: RunTrashState = RunTrashState.ACTIVE,
        status: RunStatus | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> RunPage:
        limit = min(max(1, limit), 200)
        offset = max(0, offset)
        filters = []
        if trash_state is RunTrashState.ACTIVE:
            filters.append(RunRecord.trashed_at.is_(None))
        elif trash_state is RunTrashState.TRASHED:
            filters.append(RunRecord.trashed_at.is_not(None))
        if status is not None:
            filters.append(RunRecord.status == status.value)
        if q and (query := q.strip().casefold()):
            filters.append(
                or_(
                    func.lower(RunRecord.id).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(
                        func.coalesce(
                            func.json_extract(
                                RunRecord.request_json,
                                "$.ticker",
                            ),
                            "",
                        )
                    ).contains(
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
                )
            )
        stmt = (
            select(RunRecord, DecisionRecord.rating)
            .outerjoin(DecisionRecord, DecisionRecord.run_id == RunRecord.id)
            .where(*filters)
            .order_by(RunRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        count_stmt = select(func.count()).select_from(RunRecord).where(*filters)
        with self.sessions() as session:
            return RunPage(
                items=tuple(
                    self._summary(record, rating) for record, rating in session.execute(stmt)
                ),
                total=int(session.scalar(count_stmt) or 0),
                limit=limit,
                offset=offset,
            )

    def trash_runs(
        self,
        run_ids: tuple[str, ...],
    ) -> tuple[tuple[RunView, ...], int]:
        """Atomically trash terminal runs after validating the full batch."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            records = {
                record.id: record
                for record in session.scalars(select(RunRecord).where(RunRecord.id.in_(run_ids)))
            }
            missing = [run_id for run_id in run_ids if run_id not in records]
            if missing:
                raise RunNotFoundError(", ".join(missing))
            invalid = [
                record.id
                for record in records.values()
                if record.trashed_at is None and record.status not in _TERMINAL_STATUSES
            ]
            if invalid:
                raise InvalidRunTransitionError(
                    "only terminal runs can be trashed: " + ", ".join(invalid)
                )
            changed = 0
            for run_id in run_ids:
                record = records[run_id]
                if record.trashed_at is None:
                    record.trashed_at = now
                    record.updated_at = now
                    changed += 1
            session.flush()
            views = tuple(self._view(records[run_id]) for run_id in run_ids)
        return views, changed

    def restore_runs(
        self,
        run_ids: tuple[str, ...],
    ) -> tuple[tuple[RunView, ...], int]:
        """Atomically restore trashed runs; repeated requests are idempotent."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            records = {
                record.id: record
                for record in session.scalars(select(RunRecord).where(RunRecord.id.in_(run_ids)))
            }
            missing = [run_id for run_id in run_ids if run_id not in records]
            if missing:
                raise RunNotFoundError(", ".join(missing))
            changed = 0
            for run_id in run_ids:
                record = records[run_id]
                if record.trashed_at is not None:
                    record.trashed_at = None
                    record.updated_at = now
                    changed += 1
            session.flush()
            views = tuple(self._view(records[run_id]) for run_id in run_ids)
        return views, changed

    def set_instrument_name(
        self,
        run_id: str,
        instrument_name: str | None,
    ) -> RunView:
        """Persist a best-effort display name without affecting run status."""
        normalized = (
            instrument_name.strip()[:300]
            if isinstance(instrument_name, str) and instrument_name.strip()
            else None
        )
        if normalized is None:
            return self.get_run(run_id)
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            record.instrument_name = normalized
            record.updated_at = _utc_naive()
        return self.get_run(run_id)

    def set_instrument_local_name(
        self,
        run_id: str,
        instrument_local_name: str | None,
    ) -> RunView:
        """Persist one cutoff-safe market-local display name when available."""
        normalized = (
            instrument_local_name.strip()[:300]
            if isinstance(instrument_local_name, str) and instrument_local_name.strip()
            else None
        )
        if normalized is None:
            return self.get_run(run_id)
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.instrument_local_name is None:
                record.instrument_local_name = normalized
                record.updated_at = _utc_naive()
        return self.get_run(run_id)

    def recent_instruments(self, *, limit: int = 20) -> tuple[RecentInstrument, ...]:
        """Return the latest non-trashed use of each canonical ticker."""
        limit = min(max(1, limit), 100)
        ticker = func.json_extract(
            RunRecord.request_json,
            "$.ticker",
        )
        ranked = (
            select(
                ticker.label("ticker"),
                RunRecord.instrument_name.label("instrument_name"),
                RunRecord.instrument_local_name.label("instrument_local_name"),
                RunRecord.created_at.label("last_used_at"),
                func.row_number()
                .over(
                    partition_by=func.lower(ticker),
                    order_by=(
                        RunRecord.created_at.desc(),
                        RunRecord.id.desc(),
                    ),
                )
                .label("ticker_rank"),
            )
            .where(
                RunRecord.trashed_at.is_(None),
                ticker.is_not(None),
            )
            .subquery()
        )
        stmt = (
            select(
                ranked.c.ticker,
                ranked.c.instrument_name,
                ranked.c.instrument_local_name,
                ranked.c.last_used_at,
            )
            .where(ranked.c.ticker_rank == 1)
            .order_by(ranked.c.last_used_at.desc(), ranked.c.ticker)
            .limit(limit)
        )
        with self.engine.connect() as connection:
            return tuple(
                RecentInstrument(
                    ticker=str(row.ticker),
                    instrument_name=row.instrument_name,
                    instrument_local_name=row.instrument_local_name,
                    last_used_at=_aware(row.last_used_at),
                )
                for row in connection.execute(stmt)
            )

    def claim_next(self, worker_id: str, lease_seconds: int) -> RunView | None:
        """Atomically claim queued work or recover an expired running lease."""
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            candidate = (
                connection.execute(
                    select(
                        RunRecord.id,
                        RunRecord.status,
                        RunRecord.current_attempt,
                        RunAttemptRecord.started_at.label("attempt_started_at"),
                    )
                    .join(
                        RunAttemptRecord,
                        and_(
                            RunAttemptRecord.run_id == RunRecord.id,
                            RunAttemptRecord.attempt == RunRecord.current_attempt,
                        ),
                    )
                    .where(
                        RunRecord.trashed_at.is_(None),
                        or_(
                            RunRecord.status == RunStatus.QUEUED.value,
                            and_(
                                RunRecord.status == RunStatus.RUNNING.value,
                                RunRecord.lease_expires_at < now,
                                RunRecord.cancel_requested.is_(False),
                            ),
                        ),
                    )
                    .order_by(RunRecord.created_at)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if candidate is None:
                connection.commit()
                return None
            resuming = candidate["attempt_started_at"] is not None
            connection.execute(
                update(RunRecord)
                .where(RunRecord.id == candidate["id"])
                .values(
                    status=RunStatus.RUNNING.value,
                    lease_owner=worker_id,
                    lease_expires_at=expires,
                    started_at=func.coalesce(RunRecord.started_at, now),
                    updated_at=now,
                )
            )
            attempt_values: dict[str, Any] = {
                "status": RunStatus.RUNNING.value,
                "lease_owner": worker_id,
                "lease_expires_at": expires,
                "started_at": func.coalesce(RunAttemptRecord.started_at, now),
            }
            if resuming:
                attempt_values["resume_count"] = RunAttemptRecord.resume_count + 1
            connection.execute(
                update(RunAttemptRecord)
                .where(
                    RunAttemptRecord.run_id == candidate["id"],
                    RunAttemptRecord.attempt == candidate["current_attempt"],
                )
                .values(**attempt_values)
            )
            connection.commit()
        return self.get_run(candidate["id"])

    def claim_run(self, run_id: str, worker_id: str, lease_seconds: int) -> RunView:
        """Claim a specific queued run for the synchronous Python API."""
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.trashed_at is not None:
                raise InvalidRunTransitionError(f"run {run_id} is trashed")
            if record.status != RunStatus.QUEUED.value:
                raise InvalidRunTransitionError(f"run {run_id} is {record.status}, expected queued")
            record.status = RunStatus.RUNNING.value
            record.lease_owner = worker_id
            record.lease_expires_at = expires
            record.started_at = record.started_at or now
            record.updated_at = now
            attempt = session.scalar(
                select(RunAttemptRecord).where(
                    RunAttemptRecord.run_id == run_id,
                    RunAttemptRecord.attempt == record.current_attempt,
                )
            )
            attempt.status = RunStatus.RUNNING.value
            attempt.lease_owner = worker_id
            attempt.lease_expires_at = expires
            if attempt.started_at is not None:
                attempt.resume_count += 1
            attempt.started_at = attempt.started_at or now
        return self.get_run(run_id)

    def heartbeat(self, run_id: str, worker_id: str, lease_seconds: int) -> bool:
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.sessions.begin() as session:
            changed = session.execute(
                update(RunRecord)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.status == RunStatus.RUNNING.value,
                    RunRecord.lease_owner == worker_id,
                )
                .values(lease_expires_at=expires, updated_at=now)
            ).rowcount
            if changed:
                record = session.get(RunRecord, run_id)
                session.execute(
                    update(RunAttemptRecord)
                    .where(
                        RunAttemptRecord.run_id == run_id,
                        RunAttemptRecord.attempt == record.current_attempt,
                    )
                    .values(lease_expires_at=expires)
                )
        return bool(changed)

    def release_claim(
        self,
        run_id: str,
        worker_id: str,
        *,
        metrics: RunMetrics | None = None,
    ) -> RunView:
        """Return an interrupted run to the queue without changing its attempt."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value or record.lease_owner != worker_id:
                raise InvalidRunTransitionError(f"run {run_id} is not claimed by {worker_id}")
            record.status = RunStatus.QUEUED.value
            record.lease_owner = None
            record.lease_expires_at = None
            record.updated_at = now
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.QUEUED.value
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            self._merge_metrics(record, attempt, metrics)
        return self.get_run(run_id)

    def request_cancel(self, run_id: str) -> RunView:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.trashed_at is not None:
                raise InvalidRunTransitionError(f"run {run_id} is trashed")
            if record.status == RunStatus.QUEUED.value:
                record.status = RunStatus.CANCELLED.value
                record.cancel_requested = True
                record.finished_at = now
                attempt = self._attempt(session, record)
                attempt.status = RunStatus.CANCELLED.value
                attempt.finished_at = now
            elif record.status == RunStatus.RUNNING.value:
                record.cancel_requested = True
            elif record.status not in {
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
            }:
                raise InvalidRunTransitionError(record.status)
            record.updated_at = now
        return self.get_run(run_id)

    def cancel_requested(self, run_id: str) -> bool:
        with self.sessions() as session:
            value = session.scalar(select(RunRecord.cancel_requested).where(RunRecord.id == run_id))
            if value is None:
                raise RunNotFoundError(run_id)
            return bool(value)

    def retry(self, run_id: str) -> RunView:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.trashed_at is not None:
                raise InvalidRunTransitionError(f"run {run_id} is trashed")
            if record.status != RunStatus.FAILED.value:
                raise InvalidRunTransitionError(
                    f"only failed runs can be retried, got {record.status}"
                )
            checkpoint_thread_id = self._attempt(
                session,
                record,
            ).checkpoint_thread_id
            record.current_attempt += 1
            record.status = RunStatus.QUEUED.value
            record.cancel_requested = False
            record.lease_owner = None
            record.lease_expires_at = None
            record.error_code = None
            record.error_message = None
            record.finished_at = None
            record.updated_at = now
            session.add(
                RunAttemptRecord(
                    run_id=run_id,
                    attempt=record.current_attempt,
                    status=RunStatus.QUEUED.value,
                    checkpoint_thread_id=checkpoint_thread_id,
                    metrics_json=RunMetrics().model_dump(mode="json"),
                )
            )
        return self.get_run(run_id)

    def freeze_information_frontier(
        self,
        run_id: str,
        frontier: datetime,
    ) -> datetime:
        """Persist the first usable frontier and reuse it for later attempts."""
        if frontier.utcoffset() is None:
            raise ValueError("Information Frontier requires a timezone")
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.information_frontier is None:
                record.information_frontier = frontier.isoformat()
            return datetime.fromisoformat(record.information_frontier)

    def checkpoint_thread(self, run_id: str) -> str:
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            attempt = self._attempt(session, record)
            return attempt.checkpoint_thread_id

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        node: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        now = _utc_naive()
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            row = connection.execute(
                select(RunRecord.current_attempt).where(RunRecord.id == run_id)
            ).first()
            if row is None:
                connection.rollback()
                raise RunNotFoundError(run_id)
            sequence = connection.execute(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1).where(
                    RunEventRecord.run_id == run_id
                )
            ).scalar_one()
            connection.execute(
                RunEventRecord.__table__.insert().values(
                    run_id=run_id,
                    sequence=sequence,
                    attempt=row[0],
                    event_type=event_type,
                    node=node,
                    payload_json=_sanitize_payload(payload or {}),
                    created_at=now,
                )
            )
            connection.commit()
        return RunEvent(
            run_id=run_id,
            sequence=sequence,
            attempt=row[0],
            event_type=event_type,
            node=node,
            payload=_sanitize_payload(payload or {}),
            created_at=_aware(now),
        )

    def list_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[RunEvent]:
        stmt = (
            select(RunEventRecord)
            .where(
                RunEventRecord.run_id == run_id,
                RunEventRecord.sequence > max(0, after_sequence),
            )
            .order_by(RunEventRecord.sequence)
            .limit(min(max(1, limit), 2000))
        )
        with self.sessions() as session:
            records = list(session.scalars(stmt))
        return [
            RunEvent(
                run_id=record.run_id,
                sequence=record.sequence,
                attempt=record.attempt,
                event_type=record.event_type,
                node=record.node,
                payload=record.payload_json,
                created_at=_aware(record.created_at),
            )
            for record in records
        ]

    def list_recoveries(self, run_id: str) -> tuple[StructuredRecoveryNotice, ...]:
        """Return all successful structured recoveries without event-page limits."""

        event_types = tuple(
            {
                "node.output_retry",
                "node.output_recovered",
                "node.output_failed",
                "node.numeric_audit_retry",
                "node.numeric_audit_recovered",
                "node.numeric_audit_degraded",
            }
        )
        with self.sessions() as session:
            records = tuple(
                session.scalars(
                    select(RunEventRecord)
                    .where(
                        RunEventRecord.run_id == run_id,
                        RunEventRecord.event_type.in_(event_types),
                    )
                    .order_by(RunEventRecord.sequence)
                )
            )
        events = tuple(
            RunEvent(
                run_id=record.run_id,
                sequence=record.sequence,
                attempt=record.attempt,
                event_type=record.event_type,
                node=record.node,
                payload=record.payload_json,
                created_at=_aware(record.created_at),
            )
            for record in records
        )
        return rebuild_structured_recoveries(events)

    def append_artifact(
        self,
        run_id: str,
        draft: ResearchArtifactDraft,
    ) -> tuple[ResearchArtifact, RunEvent | None]:
        """Persist one artifact and its metadata event in one transaction.

        A replay that emits the same stage output returns the existing artifact
        without creating another event, including when the replay is a later
        retry attempt.
        """
        now = _utc_naive()
        table = RunArtifactRecord.__table__
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            run_row = connection.execute(
                select(
                    RunRecord.current_attempt,
                    RunRecord.status,
                ).where(RunRecord.id == run_id)
            ).first()
            if run_row is None:
                connection.rollback()
                raise RunNotFoundError(run_id)
            if run_row.status != RunStatus.RUNNING.value:
                connection.rollback()
                raise InvalidRunTransitionError(run_row.status)
            existing = (
                connection.execute(
                    select(table).where(
                        table.c.run_id == run_id,
                        table.c.stage == draft.stage,
                        table.c.role == draft.role,
                        table.c.round == draft.round,
                        table.c.prompt_version == draft.prompt_version,
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["content_hash"] != draft.content_hash:
                    connection.rollback()
                    raise ArtifactConflictError("artifact identity replayed with different content")
                connection.commit()
                return self._artifact(existing), None

            artifact_id = str(uuid4())
            attempt = run_row.current_attempt
            connection.execute(
                table.insert().values(
                    id=artifact_id,
                    run_id=run_id,
                    attempt=attempt,
                    stage=draft.stage,
                    role=draft.role,
                    round=draft.round,
                    schema_version=draft.schema_version,
                    prompt_version=draft.prompt_version,
                    generation_method=draft.generation_method.value,
                    generation_observations_json=[
                        item.model_dump(mode="json") for item in draft.generation_observations
                    ],
                    content_type=draft.content_type,
                    content_json=draft.content.model_dump(mode="json"),
                    content_hash=draft.content_hash,
                    created_at=now,
                )
            )
            sequence = connection.execute(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1).where(
                    RunEventRecord.run_id == run_id
                )
            ).scalar_one()
            payload = {
                "artifact_id": artifact_id,
                "attempt": attempt,
                "stage": draft.stage,
                "role": draft.role,
                "round": draft.round,
                "schema_version": draft.schema_version,
                "prompt_version": draft.prompt_version,
                "generation_method": draft.generation_method.value,
                "generation_observations": [
                    item.model_dump(mode="json") for item in draft.generation_observations
                ],
                "content_type": draft.content_type,
            }
            connection.execute(
                RunEventRecord.__table__.insert().values(
                    run_id=run_id,
                    sequence=sequence,
                    attempt=attempt,
                    event_type="artifact.created",
                    node=draft.node,
                    payload_json=payload,
                    created_at=now,
                )
            )
            connection.commit()

        artifact = ResearchArtifact(
            id=artifact_id,
            run_id=run_id,
            attempt=attempt,
            stage=draft.stage,
            role=draft.role,
            round=draft.round,
            schema_version=draft.schema_version,
            prompt_version=draft.prompt_version,
            generation_method=draft.generation_method,
            generation_observations=draft.generation_observations,
            content=draft.content,
            created_at=_aware(now),
        )
        event = RunEvent(
            run_id=run_id,
            sequence=sequence,
            attempt=attempt,
            event_type="artifact.created",
            node=draft.node,
            payload=payload,
            created_at=_aware(now),
        )
        return artifact, event

    def list_artifacts(
        self,
        run_id: str,
        *,
        attempt: int | None = None,
    ) -> list[ResearchArtifact]:
        table = RunArtifactRecord.__table__
        stmt = select(table).where(table.c.run_id == run_id)
        if attempt is not None:
            stmt = stmt.where(table.c.attempt == attempt)
        stmt = stmt.order_by(table.c.created_at, table.c.id)
        with self.engine.connect() as connection:
            records = list(connection.execute(stmt).mappings())
        if not records and not self._run_exists(run_id):
            raise RunNotFoundError(run_id)
        return [self._artifact(record) for record in records]

    def seal_evidence(
        self,
        run_id: str,
        bundle: EvidenceBundle,
    ) -> tuple[EvidenceSealView, RunEvent | None]:
        """Persist the immutable evidence ledger and its event atomically."""
        now = _utc_naive()
        digest = bundle.digest
        if digest is None:  # pragma: no cover - enforced by EvidenceBundle
            raise ValueError("evidence bundle must have a digest")
        table = RunEvidenceRecord.__table__
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            run_row = connection.execute(
                select(
                    RunRecord.current_attempt,
                    RunRecord.status,
                ).where(RunRecord.id == run_id)
            ).first()
            if run_row is None:
                connection.rollback()
                raise RunNotFoundError(run_id)
            if run_row.status != RunStatus.RUNNING.value:
                connection.rollback()
                raise InvalidRunTransitionError(run_row.status)
            existing = (
                connection.execute(select(table).where(table.c.run_id == run_id)).mappings().first()
            )
            if existing is not None:
                if existing["digest"] != digest:
                    connection.rollback()
                    raise EvidenceConflictError("evidence seal replayed with a different digest")
                connection.commit()
                return self._evidence_view(existing), None

            attempt = run_row.current_attempt
            connection.execute(
                table.insert().values(
                    run_id=run_id,
                    sealed_attempt=attempt,
                    bundle_json=bundle.model_dump(mode="json"),
                    digest=digest,
                    item_count=len(bundle.items),
                    table_count=len(bundle.tables),
                    sealed_at=now,
                )
            )
            sequence = connection.execute(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1).where(
                    RunEventRecord.run_id == run_id
                )
            ).scalar_one()
            payload = {
                "attempt": attempt,
                "digest": digest,
                "item_count": len(bundle.items),
                "table_count": len(bundle.tables),
            }
            connection.execute(
                RunEventRecord.__table__.insert().values(
                    run_id=run_id,
                    sequence=sequence,
                    attempt=attempt,
                    event_type="evidence.sealed",
                    node="evidence.seal",
                    payload_json=payload,
                    created_at=now,
                )
            )
            connection.commit()
        view = EvidenceSealView(
            status="sealed",
            digest=digest,
            item_count=len(bundle.items),
            table_count=len(bundle.tables),
            sealed_attempt=attempt,
            sealed_at=_aware(now),
        )
        return (
            view,
            RunEvent(
                run_id=run_id,
                sequence=sequence,
                attempt=attempt,
                event_type="evidence.sealed",
                node="evidence.seal",
                payload=payload,
                created_at=_aware(now),
            ),
        )

    def evidence_status(self, run_id: str) -> EvidenceSealView:
        with self.engine.connect() as connection:
            run_exists = connection.scalar(
                select(func.count()).select_from(RunRecord).where(RunRecord.id == run_id)
            )
            if not run_exists:
                raise RunNotFoundError(run_id)
            record = (
                connection.execute(
                    select(RunEvidenceRecord.__table__).where(RunEvidenceRecord.run_id == run_id)
                )
                .mappings()
                .first()
            )
        return (
            self._evidence_view(record)
            if record is not None
            else EvidenceSealView(status="pending")
        )

    def get_evidence(self, run_id: str) -> EvidenceBundle:
        with self.sessions() as session:
            record = session.get(RunEvidenceRecord, run_id)
            if record is None:
                if session.get(RunRecord, run_id) is None:
                    raise RunNotFoundError(run_id)
                raise EvidenceNotSealedError(run_id)
            return EvidenceBundle.model_validate(record.bundle_json)

    def fail(
        self,
        run_id: str,
        error: BaseException,
        *,
        metrics: RunMetrics | None = None,
    ) -> RunMetrics:
        now = _utc_naive()
        code = type(error).__name__[:80]
        message = _sanitize_text(str(error)) or code
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            record.status = RunStatus.FAILED.value
            record.error_code = code
            record.error_message = message
            record.finished_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.FAILED.value
            attempt.error_code = code
            attempt.error_message = message
            attempt.finished_at = now
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            aggregate = self._merge_metrics(record, attempt, metrics)
        return aggregate

    def finish_cancel(
        self,
        run_id: str,
        *,
        metrics: RunMetrics | None = None,
    ) -> RunMetrics:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            record.status = RunStatus.CANCELLED.value
            record.finished_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.CANCELLED.value
            attempt.finished_at = now
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            aggregate = self._merge_metrics(record, attempt, metrics)
        return aggregate

    def get_result(self, run_id: str) -> AnalysisResult:
        view = self.get_run(run_id)
        artifacts = self.list_artifacts(run_id)
        with self.sessions() as session:
            decision_record = session.scalar(
                select(DecisionRecord).where(DecisionRecord.run_id == run_id)
            )
            evidence_record = session.get(RunEvidenceRecord, run_id)
        reports = order_reports(
            {
                artifact.role: artifact.content
                for artifact in artifacts
                if artifact.stage == "analyst" and isinstance(artifact.content, AnalystReport)
            }
        )
        decision = (
            ResearchDecision.model_validate(decision_record.decision_json)
            if decision_record
            else next(
                (
                    artifact.content
                    for artifact in reversed(artifacts)
                    if artifact.stage == "decision"
                    and isinstance(artifact.content, ResearchDecision)
                ),
                None,
            )
        )
        numeric_audit = (
            DecisionNumericAuditAppendix.model_validate(decision_record.numeric_audit_json)
            if decision_record and decision_record.numeric_audit_json
            else None
        )
        evidence = (
            EvidenceBundle.model_validate(evidence_record.bundle_json) if evidence_record else None
        )
        warnings = tuple(
            dict.fromkeys(
                (
                    *(
                        warning
                        for report in reports.values()
                        if isinstance(report, AnalystReport)
                        for warning in report.warnings
                    ),
                    *(
                        (
                            ResearchWarning(
                                code=(
                                    f"decision.numeric_audit_{decision.numeric_audit_status.value}"
                                ),
                                message=(_numeric_audit_warning_message(numeric_audit)),
                                source="committee.final.serialize.numeric",
                            ),
                        )
                        if decision is not None
                        and decision.numeric_audit_status
                        in {
                            NumericAuditStatus.PARTIAL,
                            NumericAuditStatus.INCOMPLETE,
                        }
                        else ()
                    ),
                )
            )
        )
        return AnalysisResult(
            run_id=run_id,
            status=view.status,
            instrument=view.request.ticker,
            instrument_name=view.instrument_name,
            instrument_local_name=view.instrument_local_name,
            reports=reports,
            decision=decision,
            numeric_audit=numeric_audit,
            evidence=evidence,
            metrics=view.metrics,
            recoveries=self.list_recoveries(run_id),
            warnings=warnings,
        )

    def list_attempts(self, run_id: str) -> tuple[RunAttemptView, ...]:
        if not self._run_exists(run_id):
            raise RunNotFoundError(run_id)
        with self.sessions() as session:
            records = tuple(
                session.scalars(
                    select(RunAttemptRecord)
                    .where(RunAttemptRecord.run_id == run_id)
                    .order_by(RunAttemptRecord.attempt)
                )
            )
        return tuple(
            RunAttemptView(
                attempt=record.attempt,
                status=RunStatus(record.status),
                resume_count=record.resume_count,
                metrics=RunMetrics.model_validate(record.metrics_json or {}),
                started_at=_aware(record.started_at),
                finished_at=_aware(record.finished_at),
                error_code=record.error_code,
            )
            for record in records
        )
