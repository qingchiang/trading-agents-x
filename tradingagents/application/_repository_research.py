"""Internal persistence store; use RunRepository as the public facade."""

from __future__ import annotations

from ._repository_common import (
    RepositoryStore,
    ResearchChain,
    ResearchChainNotFoundError,
    ResearchChainRecord,
    ResearchRevision,
    ResearchRevisionNotFoundError,
    ResearchRevisionRecord,
    select,
)


class ResearchChainStore(RepositoryStore):
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
            return tuple(self._research_chain(session, record) for record in records)

    def get_research_chain(self, chain_id: str) -> ResearchChain:
        with self.sessions() as session:
            record = session.get(ResearchChainRecord, chain_id)
            if record is None:
                raise ResearchChainNotFoundError(chain_id)
            return self._research_chain(session, record)

    def get_research_revision(self, revision_id: str) -> ResearchRevision:
        with self.sessions() as session:
            record = session.get(ResearchRevisionRecord, revision_id)
            if record is None:
                raise ResearchRevisionNotFoundError(revision_id)
            return self._research_revision(record)
