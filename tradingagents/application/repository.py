"""Transactional repository for runs, events, reports, and research memory."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.dataflows.symbol_utils import market_timezone
from tradingagents.version import __version__

from .contracts import (
    AnalysisRequest,
    AnalysisResult,
    AnalystReport,
    EvidenceBundle,
    PerspectiveReview,
    ResearchArtifact,
    ResearchArtifactDraft,
    ResearchDecision,
    RunEvent,
    RunMetrics,
    RunStatus,
    RunView,
)
from .database import (
    Base,
    DecisionRecord,
    LegacyImportRecord,
    OutcomeRecord,
    ReflectionRecord,
    ReportRecord,
    RunArtifactRecord,
    RunAttemptRecord,
    RunEventRecord,
    RunRecord,
    create_sqlite_engine,
)
from .settings import AppSettings

_SECRET_RE = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|token)(\s*[:=]\s*)(\S+)"
)


def _utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None else None


def _sanitize_text(value: str | None, limit: int = 2000) -> str | None:
    if value is None:
        return None
    redacted = _SECRET_RE.sub(r"\1\2[REDACTED]", str(value))
    return redacted[:limit]


def _sanitize_payload(value: Any, key: str = "") -> Any:
    if any(
        fragment in key.casefold()
        for fragment in ("key", "secret", "token", "password", "authorization")
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize_payload(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, key) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value, limit=10_000)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class RunNotFoundError(LookupError):
    pass


class InvalidRunTransitionError(RuntimeError):
    pass


class IdempotencyConflictError(RuntimeError):
    pass


class RunRepository:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        settings.prepare_filesystem()
        self.engine = create_sqlite_engine(
            settings.database_path,
            busy_timeout_ms=settings.busy_timeout_ms,
        )
        self.sessions = sessionmaker(
            self.engine, expire_on_commit=False, class_=Session
        )

    def create_schema(self) -> None:
        """Create the current schema for tests; production entry points run Alembic."""
        Base.metadata.create_all(self.engine)

    def create_run(
        self,
        request: AnalysisRequest,
        config_snapshot: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        parent_run_id: str | None = None,
    ) -> tuple[RunView, bool]:
        now = _utc_naive()
        request_json = request.model_dump(mode="json")
        try:
            with self.sessions.begin() as session:
                if idempotency_key:
                    existing = session.scalar(
                        select(RunRecord).where(
                            RunRecord.idempotency_key == idempotency_key
                        )
                    )
                    if existing is not None:
                        if existing.request_json != request_json:
                            raise IdempotencyConflictError(
                                "idempotency key was already used for a "
                                "different request"
                            )
                        return self._view(existing), False
                run_id = str(uuid4())
                record = RunRecord(
                    id=run_id,
                    parent_run_id=parent_run_id,
                    idempotency_key=idempotency_key,
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
                    )
                )
        except IntegrityError as exc:
            if idempotency_key is None:
                raise
            with self.sessions() as session:
                existing = session.scalar(
                    select(RunRecord).where(
                        RunRecord.idempotency_key == idempotency_key
                    )
                )
                if existing is None:
                    raise
                if existing.request_json != request_json:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    ) from exc
                return self._view(existing), False
        return self.get_run(run_id), True

    @staticmethod
    def checkpoint_thread_id(run_id: str, attempt: int) -> str:
        return f"run:{run_id}:attempt:{attempt}"

    def get_run(self, run_id: str) -> RunView:
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            return self._view(record)

    def list_runs(
        self,
        *,
        status: RunStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunView]:
        stmt = select(RunRecord).order_by(RunRecord.created_at.desc())
        if status is not None:
            stmt = stmt.where(RunRecord.status == status.value)
        stmt = stmt.offset(max(0, offset)).limit(min(max(1, limit), 200))
        with self.sessions() as session:
            return [self._view(record) for record in session.scalars(stmt)]

    def claim_next(self, worker_id: str, lease_seconds: int) -> RunView | None:
        """Atomically claim queued work or recover an expired running lease."""
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            candidate = connection.execute(
                select(
                    RunRecord.id,
                    RunRecord.status,
                    RunRecord.current_attempt,
                )
                .where(
                    or_(
                        RunRecord.status == RunStatus.QUEUED.value,
                        and_(
                            RunRecord.status == RunStatus.RUNNING.value,
                            RunRecord.lease_expires_at < now,
                            RunRecord.cancel_requested.is_(False),
                        ),
                    )
                )
                .order_by(RunRecord.created_at)
                .limit(1)
            ).mappings().first()
            if candidate is None:
                connection.commit()
                return None
            recovering = candidate["status"] == RunStatus.RUNNING.value
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
            if recovering:
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

    def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> RunView:
        """Claim a specific queued run for the synchronous Python API."""
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.QUEUED.value:
                raise InvalidRunTransitionError(
                    f"run {run_id} is {record.status}, expected queued"
                )
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

    def request_cancel(self, run_id: str) -> RunView:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
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
            value = session.scalar(
                select(RunRecord.cancel_requested).where(RunRecord.id == run_id)
            )
            if value is None:
                raise RunNotFoundError(run_id)
            return bool(value)

    def retry(self, run_id: str) -> RunView:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
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
                )
            )
        return self.get_run(run_id)

    def checkpoint_thread(self, run_id: str) -> str:
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            attempt = self._attempt(session, record)
            return attempt.checkpoint_thread_id

    def rerun(self, run_id: str) -> RunView:
        source = self.get_run(run_id)
        request = source.request
        view, _ = self.create_run(
            request,
            source.config_snapshot,
            parent_run_id=run_id,
        )
        return view

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
            sequence = (
                connection.execute(
                    select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1)
                    .where(RunEventRecord.run_id == run_id)
                ).scalar_one()
            )
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
                        table.c.content_hash == draft.content_hash,
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
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
                    content_type=draft.content_type,
                    content_json=draft.content.model_dump(mode="json"),
                    content_hash=draft.content_hash,
                    created_at=now,
                )
            )
            sequence = connection.execute(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1)
                .where(RunEventRecord.run_id == run_id)
            ).scalar_one()
            payload = {
                "artifact_id": artifact_id,
                "attempt": attempt,
                "stage": draft.stage,
                "role": draft.role,
                "round": draft.round,
                "schema_version": draft.schema_version,
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

    def complete(
        self,
        run_id: str,
        result: AnalysisResult,
        *,
        evidence: EvidenceBundle,
        benchmark: str,
    ) -> None:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value:
                raise InvalidRunTransitionError(record.status)
            for name, report in result.reports.items():
                if hasattr(report, "model_dump"):
                    structured = report.model_dump(mode="json")
                    markdown = getattr(report, "narrative", json.dumps(structured))
                else:
                    structured = None
                    markdown = str(report)
                session.add(
                    ReportRecord(
                        run_id=run_id,
                        name=name,
                        markdown=markdown,
                        structured_json=structured,
                        created_at=now,
                    )
                )
            if result.decision is not None:
                request = AnalysisRequest.model_validate(record.request_json)
                market = self.market_bucket(
                    request.ticker, request.asset_type.value
                )
                decision = DecisionRecord(
                    run_id=run_id,
                    ticker=request.ticker,
                    market=market,
                    asset_type=request.asset_type.value,
                    analysis_date=request.analysis_date,
                    rating=result.decision.rating.value,
                    confidence=result.decision.confidence,
                    decision_json=result.decision.model_dump(mode="json"),
                    evidence_bundle_json=evidence.model_dump(mode="json"),
                    created_at=now,
                )
                session.add(decision)
                session.flush()
                session.add(
                    OutcomeRecord(
                        decision_id=decision.id,
                        status="pending",
                        benchmark=benchmark,
                        holding_intervals=5,
                    )
                )
            record.status = RunStatus.SUCCEEDED.value
            record.metrics_json = result.metrics.model_dump(mode="json")
            record.finished_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.SUCCEEDED.value
            attempt.finished_at = now
            attempt.lease_owner = None
            attempt.lease_expires_at = None

    def fail(self, run_id: str, error: BaseException) -> None:
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

    def finish_cancel(self, run_id: str) -> None:
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

    def get_result(self, run_id: str) -> AnalysisResult:
        view = self.get_run(run_id)
        with self.sessions() as session:
            report_records = list(
                session.scalars(
                    select(ReportRecord)
                    .where(ReportRecord.run_id == run_id)
                    .order_by(ReportRecord.id)
                )
            )
            decision_record = session.scalar(
                select(DecisionRecord).where(DecisionRecord.run_id == run_id)
            )
        reports: dict[str, Any] = {}
        for report in report_records:
            if report.structured_json and report.name in {
                "market",
                "social",
                "news",
                "fundamentals",
            }:
                reports[report.name] = AnalystReport.model_validate(
                    report.structured_json
                )
            else:
                reports[report.name] = report.markdown
        decision = (
            ResearchDecision.model_validate(decision_record.decision_json)
            if decision_record
            else None
        )
        warnings = tuple(
            dict.fromkeys(
                warning
                for report in reports.values()
                if isinstance(report, AnalystReport)
                for warning in report.warnings
            )
        )
        return AnalysisResult(
            run_id=run_id,
            status=view.status,
            instrument=view.request.ticker,
            reports=reports,
            decision=decision,
            metrics=view.metrics,
            warnings=warnings,
        )

    def _run_exists(self, run_id: str) -> bool:
        with self.engine.connect() as connection:
            return (
                connection.execute(
                    select(RunRecord.id).where(RunRecord.id == run_id)
                ).first()
                is not None
            )

    @staticmethod
    def _artifact(record: Any) -> ResearchArtifact:
        content_models = {
            "analyst_report": AnalystReport,
            "perspective_review": PerspectiveReview,
            "research_decision": ResearchDecision,
        }
        model = content_models.get(record["content_type"])
        if model is None:
            raise ValueError(
                f"unsupported research artifact type: {record['content_type']}"
            )
        return ResearchArtifact(
            id=record["id"],
            run_id=record["run_id"],
            attempt=record["attempt"],
            stage=record["stage"],
            role=record["role"],
            round=record["round"],
            schema_version=record["schema_version"],
            content=model.model_validate(record["content_json"]),
            created_at=_aware(record["created_at"]),
        )

    def pending_outcomes(self, limit: int = 20) -> list[dict[str, Any]]:
        stmt = (
            select(OutcomeRecord, DecisionRecord)
            .join(DecisionRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .where(OutcomeRecord.status == "pending")
            .order_by(DecisionRecord.analysis_date)
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
                }
                for outcome, decision in session.execute(stmt)
            ]

    def mark_outcome_checked(
        self, outcome_id: int, error_message: str | None = None
    ) -> None:
        with self.sessions.begin() as session:
            outcome = session.get(OutcomeRecord, outcome_id)
            if outcome is None:
                return
            outcome.last_checked_at = _utc_naive()
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
        now = _utc_naive()
        with self.sessions.begin() as session:
            outcome = session.get(OutcomeRecord, outcome_id)
            if outcome is None or outcome.status != "pending":
                return
            outcome.status = "resolved"
            outcome.observation_start = observation_start
            outcome.observation_end = observation_end
            outcome.raw_return = raw_return
            outcome.alpha_return = alpha_return
            outcome.last_checked_at = now
            outcome.resolved_at = now
            outcome.error_message = None
            session.add(
                ReflectionRecord(
                    outcome_id=outcome.id,
                    text=reflection,
                    created_at=now,
                )
            )

    def memory_context(
        self,
        ticker: str,
        asset_type: str,
        *,
        same_limit: int = 5,
        cross_limit: int = 3,
    ) -> str:
        market = self.market_bucket(ticker, asset_type)
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
            .where(
                OutcomeRecord.status == "resolved",
                OutcomeRecord.holding_intervals >= 5,
            )
            .order_by(
                OutcomeRecord.resolved_at.desc(),
                OutcomeRecord.id.desc(),
            )
        )
        with self.sessions() as session:
            rows = list(session.execute(resolved))
        same = [
            row
            for row in rows
            if row[0].ticker == ticker
        ][:same_limit]
        cross = [
            row
            for row in rows
            if row[0].ticker != ticker
            and row[0].asset_type == asset_type
            and market is not None
            and row[0].market == market
        ][:cross_limit]
        sections: list[str] = []
        if same:
            sections.append(f"Past analyses of {ticker} (most recent first):")
            for decision, outcome, reflection in same:
                sections.append(
                    "\n".join(
                        [
                            (
                                f"[{decision.analysis_date} | {decision.ticker} | "
                                f"{decision.rating} | {outcome.raw_return:+.1%} | "
                                f"{outcome.alpha_return:+.1%} | "
                                f"{outcome.holding_intervals}d]"
                            ),
                            f"DECISION:\n{json.dumps(decision.decision_json, ensure_ascii=False)}",
                            f"REFLECTION:\n{reflection.text}",
                        ]
                    )
                )
        if cross:
            sections.append("Recent cross-ticker lessons:")
            for decision, outcome, reflection in cross:
                sections.append(
                    f"[{decision.analysis_date} | {decision.ticker} | "
                    f"{decision.rating} | {outcome.raw_return:+.1%}]\n"
                    f"{reflection.text}"
                )
        return "\n\n".join(sections)

    def memory_entries(
        self,
        *,
        ticker: str | None = None,
        market: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(DecisionRecord, OutcomeRecord, ReflectionRecord)
            .join(OutcomeRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .outerjoin(
                ReflectionRecord,
                ReflectionRecord.outcome_id == OutcomeRecord.id,
            )
            .order_by(
                DecisionRecord.created_at.desc(),
                DecisionRecord.id.desc(),
            )
            .limit(min(max(1, limit), 500))
        )
        if ticker:
            stmt = stmt.where(DecisionRecord.ticker == ticker)
        if market:
            stmt = stmt.where(DecisionRecord.market == market)
        if status:
            stmt = stmt.where(OutcomeRecord.status == status)
        with self.sessions() as session:
            return [
                {
                    "run_id": decision.run_id,
                    "ticker": decision.ticker,
                    "market": decision.market,
                    "asset_type": decision.asset_type,
                    "analysis_date": decision.analysis_date.isoformat(),
                    "decision": decision.decision_json,
                    "outcome": {
                        "status": outcome.status,
                        "benchmark": outcome.benchmark,
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
                    },
                    "reflection": reflection.text if reflection else None,
                }
                for decision, outcome, reflection in session.execute(stmt)
            ]

    def record_legacy_import(
        self,
        source_path: str,
        content_hash: str,
        status: str,
        *,
        run_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        with self.sessions.begin() as session:
            existing = session.scalar(
                select(LegacyImportRecord).where(
                    LegacyImportRecord.content_hash == content_hash
                )
            )
            if existing:
                return False
            session.add(
                LegacyImportRecord(
                    source_path=source_path,
                    content_hash=content_hash,
                    status=status,
                    run_id=run_id,
                    error_message=_sanitize_text(error_message),
                    imported_at=_utc_naive(),
                )
            )
        return True

    def has_legacy_import(self, content_hash: str) -> bool:
        with self.sessions() as session:
            return (
                session.scalar(
                    select(LegacyImportRecord.id).where(
                        LegacyImportRecord.content_hash == content_hash
                    )
                )
                is not None
            )

    def import_legacy_memory(
        self,
        *,
        source_path: str,
        content_hash: str,
        request: AnalysisRequest,
        decision: ResearchDecision,
        benchmark: str,
        raw_return: float | None,
        alpha_return: float | None,
        holding_intervals: int,
        observation_start,
        observation_end,
        reflection: str,
    ) -> str | None:
        """Atomically import one legacy block and its optional resolved outcome."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            if session.scalar(
                select(LegacyImportRecord.id).where(
                    LegacyImportRecord.content_hash == content_hash
                )
            ):
                return None
            run_id = str(uuid4())
            session.add(
                RunRecord(
                    id=run_id,
                    status=RunStatus.SUCCEEDED.value,
                    request_json=request.model_dump(mode="json"),
                    config_json={"legacy_import": True},
                    version=__version__,
                    current_attempt=1,
                    cancel_requested=False,
                    metrics_json=RunMetrics().model_dump(mode="json"),
                    created_at=now,
                    started_at=now,
                    finished_at=now,
                    updated_at=now,
                )
            )
            session.add(
                RunAttemptRecord(
                    run_id=run_id,
                    attempt=1,
                    status=RunStatus.SUCCEEDED.value,
                    checkpoint_thread_id=self.checkpoint_thread_id(run_id, 1),
                    started_at=now,
                    finished_at=now,
                )
            )
            session.flush()
            decision_record = DecisionRecord(
                run_id=run_id,
                ticker=request.ticker,
                market=self.market_bucket(
                    request.ticker, request.asset_type.value
                ),
                asset_type=request.asset_type.value,
                analysis_date=request.analysis_date,
                rating=decision.rating.value,
                confidence=decision.confidence,
                decision_json=decision.model_dump(mode="json"),
                evidence_bundle_json=EvidenceBundle(
                    instrument=request.ticker,
                    analysis_date=request.analysis_date,
                    items=(),
                ).model_dump(mode="json"),
                created_at=now,
            )
            session.add(decision_record)
            session.flush()
            resolved = raw_return is not None and alpha_return is not None
            outcome = OutcomeRecord(
                decision_id=decision_record.id,
                status="resolved" if resolved else "pending",
                benchmark=benchmark,
                observation_start=observation_start,
                observation_end=observation_end,
                holding_intervals=holding_intervals,
                raw_return=raw_return,
                alpha_return=alpha_return,
                last_checked_at=now if resolved else None,
                resolved_at=now if resolved else None,
            )
            session.add(outcome)
            session.flush()
            if reflection:
                session.add(
                    ReflectionRecord(
                        outcome_id=outcome.id,
                        text=reflection,
                        created_at=now,
                    )
                )
            session.add(
                LegacyImportRecord(
                    source_path=source_path,
                    content_hash=content_hash,
                    status="imported",
                    run_id=run_id,
                    imported_at=now,
                )
            )
        return run_id

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

    @staticmethod
    def market_bucket(ticker: str, asset_type: str) -> str | None:
        if asset_type == "crypto":
            return "CRYPTO"
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
    def _view(record: RunRecord) -> RunView:
        return RunView(
            id=record.id,
            parent_run_id=record.parent_run_id,
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
            updated_at=_aware(record.updated_at),
        )
