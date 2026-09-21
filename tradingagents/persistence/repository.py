"""SQLite repository with cohesive internal transaction/query implementations."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.configuration.settings import AppSettings
from tradingagents.domain.artifacts import (
    ArtifactGenerationObservation,
    ResearchArtifact,
)
from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    ResearchConfidenceLevel,
    ResearchRating,
    RunStatus,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.history import (
    RunSummaryView,
)
from tradingagents.domain.instruments import market_timezone
from tradingagents.domain.metrics import merge_run_metrics
from tradingagents.domain.reports import (
    AnalystReport,
    DebateAgenda,
    DecisionBrief,
    JudgeDraft,
    RebuttalReview,
    ResearchCase,
    RiskReview,
)
from tradingagents.domain.runs import (
    EvidenceSealView,
    RunMetrics,
    RunRequestSnapshot,
    RunView,
)
from tradingagents.persistence._artifacts import ArtifactsOperations
from tradingagents.persistence._execution import ExecutionOperations
from tradingagents.persistence._lifecycle import LifecycleOperations
from tradingagents.persistence._queries import QueriesOperations
from tradingagents.persistence._repository_common import (
    InvalidRunTransitionError,
    _aware,
)
from tradingagents.persistence._research import ResearchOperations
from tradingagents.persistence._timelines import TimelinesOperations
from tradingagents.persistence.backup import backup_sqlite_database
from tradingagents.persistence.models import (
    Base,
    ResearchNodeRecord,
    RunAttemptRecord,
    RunRecord,
    create_sqlite_engine,
)


class RunRepository(ExecutionOperations, LifecycleOperations, ArtifactsOperations, ResearchOperations, TimelinesOperations, QueriesOperations):
    def __init__(self, settings: AppSettings):
        self.settings = settings
        settings.prepare_filesystem()
        self.engine = create_sqlite_engine(
            settings.database_path,
            busy_timeout_ms=settings.busy_timeout_ms,
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, class_=Session)

    def create_schema(self) -> None:
        """Create the current schema for tests; production entry points run Alembic."""
        Base.metadata.create_all(self.engine)

    @staticmethod
    def checkpoint_thread_id(run_id: str, attempt: int) -> str:
        return f"run:{run_id}:attempt:{attempt}"

    @staticmethod
    def _require_retryable(record: RunRecord) -> None:
        if record.trashed_at is not None:
            raise InvalidRunTransitionError(f"run {record.id} is trashed")
        if record.status != RunStatus.FAILED.value:
            raise InvalidRunTransitionError(f"only failed runs can be retried, got {record.status}")

    @staticmethod
    def _active_incremental_slot(
        session: Session,
        full_baseline_run_id: str,
        incremental_cutoff: date,
    ) -> RunRecord | None:
        return session.scalar(
            select(RunRecord).where(
                RunRecord.research_kind == "incremental",
                RunRecord.full_baseline_run_id == full_baseline_run_id,
                RunRecord.incremental_cutoff == incremental_cutoff,
                RunRecord.trashed_at.is_(None),
                RunRecord.status.in_(
                    (
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.SUCCEEDED.value,
                    )
                ),
            )
        )

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

    def backup(self, destination: Path) -> Path:
        return backup_sqlite_database(self.settings, destination)

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
    def _view(
        record: RunRecord,
        *,
        is_research_node: bool = False,
    ) -> RunView:
        return RunView(
            id=record.id,
            source_run_id=record.source_run_id,
            is_research_node=is_research_node,
            research_schema_version=record.research_schema_version,
            information_cutoff_at=_aware(record.information_cutoff_at),
            method_snapshot=record.method_snapshot_json,
            research_kind=record.research_kind,
            full_baseline_run_id=record.full_baseline_run_id,
            instrument_name=record.instrument_name,
            instrument_local_name=record.instrument_local_name,
            status=RunStatus(record.status),
            request=RunRequestSnapshot.model_validate(record.request_json),
            config_snapshot=record.config_json,
            audit_snapshot=record.audit_snapshot_json,
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
    def _view_for_session(
        cls,
        session: Session,
        record: RunRecord,
    ) -> RunView:
        instrument_name, instrument_local_name = cls._effective_instrument_names(
            session,
            record,
        )
        return cls._view(
            record,
            is_research_node=(session.get(ResearchNodeRecord, record.id) is not None),
        ).model_copy(
            update={
                "instrument_name": instrument_name,
                "instrument_local_name": instrument_local_name,
            }
        )

    @staticmethod
    def _effective_instrument_names(
        session: Session,
        record: RunRecord,
    ) -> tuple[str | None, str | None]:
        instrument_name = record.instrument_name
        instrument_local_name = record.instrument_local_name
        if (
            record.research_kind == "incremental"
            and record.full_baseline_run_id
            and (instrument_name is None or instrument_local_name is None)
        ):
            baseline = session.get(RunRecord, record.full_baseline_run_id)
            if baseline is not None:
                instrument_name = instrument_name or baseline.instrument_name
                instrument_local_name = instrument_local_name or baseline.instrument_local_name
        return instrument_name, instrument_local_name

    @classmethod
    def _summary(
        cls,
        record: RunRecord,
        rating: str | None,
        confidence: str | None,
        is_research_node: bool,
        *,
        instrument_name: str | None = None,
        instrument_local_name: str | None = None,
    ) -> RunSummaryView:
        return RunSummaryView(
            **cls._view(
                record,
                is_research_node=is_research_node,
            )
            .model_copy(
                update={
                    "instrument_name": instrument_name,
                    "instrument_local_name": instrument_local_name,
                }
            )
            .model_dump(),
            research_rating=ResearchRating(rating) if rating else None,
            research_confidence=(ResearchConfidenceLevel(confidence) if confidence else None),
        )
