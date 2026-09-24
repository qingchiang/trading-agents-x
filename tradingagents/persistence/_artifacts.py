"""Internal artifacts operations on a shared repository session."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from tradingagents.domain.artifacts import (
    ResearchArtifact,
    ResearchArtifactDraft,
)
from tradingagents.domain.common import (
    RunStatus,
)
from tradingagents.domain.evidence import EvidenceBundle
from tradingagents.domain.recoveries import rebuild_structured_recoveries
from tradingagents.domain.runs import (
    EvidenceSealView,
    RunEvent,
    StructuredRecoveryNotice,
)
from tradingagents.persistence._repository_common import (
    ArtifactConflictError,
    EvidenceConflictError,
    EvidenceNotSealedError,
    InvalidRunTransitionError,
    RunNotFoundError,
    _aware,
    _sanitize_payload,
    _utc_naive,
)
from tradingagents.persistence.models import (
    RunArtifactRecord,
    RunEventRecord,
    RunEvidenceRecord,
    RunRecord,
)


class ArtifactsOperations:
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
