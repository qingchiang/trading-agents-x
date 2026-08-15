"""Research Chain and immutable Revision persistence mechanics."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ._repository_common import (
    InvalidResearchBaselineError,
    ResearchChainNotFoundError,
    ResearchRevisionNotFoundError,
)
from .contracts import AnalysisRequest, ResearchUpdateAudit, RunMetrics
from .database import ResearchChainRecord, ResearchRevisionRecord, RunRecord
from .research import (
    CoverageAttestation,
    CurrentResearchState,
    EffectiveEvidenceSnapshot,
    IndeterminateReason,
    ResearchChain,
    ResearchChangeConclusion,
    ResearchExecutionStrategy,
    ResearchRevision,
    ResearchRevisionDraft,
    ResearchRevisionRole,
    RevisionDelta,
    UpdateSummary,
)
from .settings import AppSettings


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None else None


class ResearchChainStore:
    """Own Research Chain/Revision records while callers own transactions."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        engine: Engine,
        sessions: sessionmaker[Session],
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.sessions = sessions

    def list_research_chains(
        self,
        *,
        instrument: str | None = None,
    ) -> tuple[ResearchChain, ...]:
        stmt = select(ResearchChainRecord)
        if instrument is not None:
            stmt = stmt.where(ResearchChainRecord.instrument == instrument)
        stmt = stmt.order_by(
            ResearchChainRecord.is_primary.desc(),
            ResearchChainRecord.created_at,
        )
        with self.sessions() as session:
            records = tuple(session.scalars(stmt))
            return tuple(self.hydrate_chain(session, record) for record in records)

    def get_research_chain(self, chain_id: str) -> ResearchChain:
        with self.sessions() as session:
            record = session.get(ResearchChainRecord, chain_id)
            if record is None:
                raise ResearchChainNotFoundError(chain_id)
            return self.hydrate_chain(session, record)

    def get_research_revision(self, revision_id: str) -> ResearchRevision:
        with self.sessions() as session:
            record = session.get(ResearchRevisionRecord, revision_id)
            if record is None:
                raise ResearchRevisionNotFoundError(revision_id)
            return self.hydrate_revision(record)

    @staticmethod
    def create_initial_revision(
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
            ResearchChainStore.revision_record(
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
    def advance_research_chain(
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
            ResearchChainStore.revision_record(
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
    def revision_record(
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

    def hydrate_chain(
        self,
        session: Session,
        record: ResearchChainRecord,
    ) -> ResearchChain:
        revisions = tuple(
            self.hydrate_revision(item)
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
    def hydrate_revision(record: ResearchRevisionRecord) -> ResearchRevision:
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
