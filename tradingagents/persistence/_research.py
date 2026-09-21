"""Internal research operations on a shared repository session."""
from __future__ import annotations

from sqlalchemy import func, select

from tradingagents.domain.common import (
    CURRENT_RESEARCH_SCHEMA_VERSION,
    RunStatus,
)
from tradingagents.domain.errors import (
    InvalidIncrementalBaselineError,
)
from tradingagents.domain.evidence import EvidenceBundle
from tradingagents.domain.incremental import (
    IncrementalNodeProducts,
)
from tradingagents.domain.runs import (
    AnalysisRequest,
    AnalysisResult,
    RunMetrics,
    RunRequestSnapshot,
    RunView,
)
from tradingagents.persistence._repository_common import (
    EvidenceConflictError,
    EvidenceNotSealedError,
    InvalidRunTransitionError,
    RunNotFoundError,
    _utc_naive,
)
from tradingagents.persistence.models import (
    DecisionRecord,
    PrimaryResearchCycleRecord,
    ResearchNodeRecord,
    RunEventRecord,
    RunEvidenceRecord,
    RunRecord,
)


class ResearchOperations:
    def validate_incremental_baseline(
        self,
        full_baseline_run_id: str,
        request: AnalysisRequest,
    ) -> RunView:
        """Return one active compatible Full Baseline or fail before a Run starts."""
        with self.sessions() as session:
            row = session.execute(
                select(RunRecord, ResearchNodeRecord)
                .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                .where(RunRecord.id == full_baseline_run_id)
            ).one_or_none()
        if row is None:
            raise InvalidIncrementalBaselineError("Full Baseline was not found")
        run, node = row
        baseline_request = RunRequestSnapshot.model_validate(run.request_json)
        if node.research_kind != "full":
            raise InvalidIncrementalBaselineError("Full Baseline must be a Full Research Node")
        if run.status != RunStatus.SUCCEEDED.value or run.trashed_at is not None:
            raise InvalidIncrementalBaselineError("Full Baseline must be active")
        if baseline_request.ticker != request.ticker:
            raise InvalidIncrementalBaselineError("Full Baseline must use the same Instrument Key")
        if run.research_schema_version != CURRENT_RESEARCH_SCHEMA_VERSION:
            raise InvalidIncrementalBaselineError(
                "Full Baseline has an incompatible Research Schema Version"
            )
        if baseline_request.analysis_date >= request.analysis_date:
            raise InvalidIncrementalBaselineError(
                "Incremental cutoff must be later than its Full Baseline"
            )
        return self._view(run, is_research_node=True)

    def complete(
        self,
        run_id: str,
        result: AnalysisResult,
        *,
        evidence: EvidenceBundle,
    ) -> RunMetrics:
        """Persist a terminal result without creating legacy review state."""
        now = _utc_naive()
        with self.sessions.begin() as session:
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
            is_post_redesign_full = (
                record.research_schema_version is not None and record.research_kind == "full"
            )
            if is_post_redesign_full and result.decision is None:
                raise ValueError("Full Research Node requires a complete Research Decision")
            if result.decision is not None:
                request = RunRequestSnapshot.model_validate(record.request_json)
                market = self.market_bucket(request.ticker)
                decision = DecisionRecord(
                    run_id=run_id,
                    ticker=request.ticker,
                    market=market,
                    asset_type=request.asset_type,
                    analysis_date=request.analysis_date,
                    rating=result.decision.rating.value,
                    confidence=result.decision.confidence.value,
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
            if is_post_redesign_full:
                node = ResearchNodeRecord(
                    run_id=run_id,
                    research_kind="full",
                    full_baseline_run_id=None,
                    created_at=now,
                )
                session.add(node)
                request = RunRequestSnapshot.model_validate(record.request_json)
                primary = session.get(PrimaryResearchCycleRecord, request.ticker)
                if primary is None:
                    session.add(
                        PrimaryResearchCycleRecord(
                            instrument=request.ticker,
                            full_run_id=run_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                elif request.make_primary is None:
                    raise ValueError("later Full Research requires an explicit make_primary choice")
                elif request.make_primary:
                    primary.full_run_id = run_id
                    primary.updated_at = now
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
        return aggregate

    def complete_incremental(
        self,
        run_id: str,
        result: AnalysisResult,
        *,
        evidence: EvidenceBundle,
        products: IncrementalNodeProducts,
    ) -> RunMetrics:
        """Atomically commit an Incremental Node and every required product."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value:
                raise InvalidRunTransitionError(record.status)
            if record.research_kind != "incremental" or record.full_baseline_run_id is None:
                raise ValueError("Incremental commit requires an Incremental Run")
            if result.decision is None:
                raise ValueError("Incremental Node requires a complete Research Decision")
            if products.decision_outcome is None:
                raise ValueError("New Incremental Nodes must record a Decision outcome")
            if session.get(RunEvidenceRecord, run_id) is not None:
                raise EvidenceConflictError("Incremental evidence was already sealed")
            digest = evidence.digest
            if digest is None:
                raise ValueError("evidence bundle must have a digest")
            request = RunRequestSnapshot.model_validate(record.request_json)
            baseline = session.get(RunRecord, record.full_baseline_run_id)
            baseline_node = session.get(ResearchNodeRecord, record.full_baseline_run_id)
            baseline_evidence = session.get(RunEvidenceRecord, record.full_baseline_run_id)
            if (
                baseline is None
                or baseline_node is None
                or baseline_evidence is None
                or baseline.status != RunStatus.SUCCEEDED.value
                or baseline.trashed_at is not None
                or baseline_node.research_kind != "full"
            ):
                raise InvalidIncrementalBaselineError(
                    "Incremental commit requires an active sealed Full Baseline"
                )
            baseline_items = {
                item.ref: item
                for item in EvidenceBundle.model_validate(baseline_evidence.bundle_json).items
            }
            baseline_request = RunRequestSnapshot.model_validate(baseline.request_json)
            if baseline_request.ticker != request.ticker:
                raise InvalidIncrementalBaselineError(
                    "Full Baseline must use the same Instrument Key at commit"
                )
            if baseline.research_schema_version != CURRENT_RESEARCH_SCHEMA_VERSION:
                raise InvalidIncrementalBaselineError(
                    "Full Baseline has an incompatible Research Schema Version at commit"
                )
            if baseline_request.analysis_date >= request.analysis_date:
                raise InvalidIncrementalBaselineError(
                    "Incremental cutoff must remain later than its Full Baseline at commit"
                )
            for item in evidence.items:
                if item.ref in baseline_items:
                    raise EvidenceConflictError(
                        "Incremental Evidence bundle must not copy Full Baseline Evidence references"
                    )
            allowed_evidence_refs = set(baseline_items)
            allowed_evidence_refs.update(item.ref for item in evidence.items)
            current_evidence_refs = {item.ref for item in evidence.items}
            for domain in products.collection_summary.domains:
                if not set(domain.evidence_refs).issubset(current_evidence_refs):
                    raise EvidenceConflictError(
                        "Collection Summary references evidence outside the current "
                        "Incremental bundle"
                    )
            if not set(result.decision.evidence_refs).issubset(allowed_evidence_refs):
                raise EvidenceConflictError(
                    "Incremental Decision references evidence outside its closure"
                )
            for entry in products.reassessment.entries:
                if not set(entry.evidence_refs).issubset(allowed_evidence_refs):
                    raise EvidenceConflictError(
                        "Incremental Reassessment references evidence outside its closure"
                    )
            for reason in products.full_research_required_reasons:
                if not set(reason.evidence_refs).issubset(allowed_evidence_refs):
                    raise EvidenceConflictError(
                        "Full Research Required references evidence outside its closure"
                    )
            session.add(
                RunEvidenceRecord(
                    run_id=run_id,
                    sealed_attempt=record.current_attempt,
                    bundle_json=evidence.model_dump(mode="json"),
                    digest=digest,
                    item_count=len(evidence.items),
                    table_count=len(evidence.tables),
                    sealed_at=now,
                )
            )
            session.add(
                DecisionRecord(
                    run_id=run_id,
                    ticker=request.ticker,
                    market=self.market_bucket(request.ticker),
                    asset_type=request.asset_type,
                    analysis_date=request.analysis_date,
                    rating=result.decision.rating.value,
                    confidence=result.decision.confidence.value,
                    decision_json=result.decision.model_dump(mode="json"),
                    numeric_audit_json=None,
                    created_at=now,
                )
            )
            session.add(
                ResearchNodeRecord(
                    run_id=run_id,
                    research_kind="incremental",
                    full_baseline_run_id=record.full_baseline_run_id,
                    created_at=now,
                    incremental_products_json=products.model_dump(mode="json"),
                )
            )
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
            sequence = session.scalar(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1).where(
                    RunEventRecord.run_id == run_id
                )
            )
            session.add_all(
                [
                    RunEventRecord(
                        run_id=run_id,
                        sequence=sequence,
                        attempt=record.current_attempt,
                        event_type="evidence.sealed",
                        node="evidence.seal",
                        payload_json={
                            "attempt": record.current_attempt,
                            "digest": digest,
                            "item_count": len(evidence.items),
                            "table_count": len(evidence.tables),
                        },
                        created_at=now,
                    ),
                    RunEventRecord(
                        run_id=run_id,
                        sequence=sequence + 1,
                        attempt=record.current_attempt,
                        event_type="run.succeeded",
                        node=None,
                        payload_json={"metrics": aggregate.model_dump(mode="json")},
                        created_at=now,
                    ),
                ]
            )
        return aggregate
