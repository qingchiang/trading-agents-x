"""Transactional repository for runs, events, reports, and research history."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.dataflows.symbol_utils import market_timezone
from tradingagents.persistence.backup import backup_sqlite_database
from tradingagents.version import __version__

from .contracts import (
    CURRENT_RESEARCH_SCHEMA_VERSION,
    AnalysisRequest,
    AnalysisResult,
    AnalystReport,
    ArtifactGenerationMethod,
    ArtifactGenerationObservation,
    DebateAgenda,
    DecisionBrief,
    DecisionNumericAuditAppendix,
    EvidenceBundle,
    EvidenceSealView,
    FullBaselineCandidate,
    IncrementalBaselineContext,
    IncrementalExportContext,
    IncrementalNodeProducts,
    IncrementalRunContext,
    JudgeDraft,
    NumericAuditStatus,
    PrimaryCycleCandidate,
    RebuttalReview,
    RecentInstrument,
    ResearchArtifact,
    ResearchArtifactDraft,
    ResearchCase,
    ResearchCycleView,
    ResearchDecision,
    ResearchNodeComparison,
    ResearchNodeComparisonSelection,
    ResearchNodeComparisonSide,
    ResearchNodeComparisonValue,
    ResearchNodeComparisonWarning,
    ResearchNodeDecisionSection,
    ResearchNodeLifecycleState,
    ResearchNodeView,
    ResearchRating,
    ResearchTimeline,
    ResearchTimelinePage,
    ResearchTimelineSummary,
    ResearchWarning,
    RiskReview,
    RunAttemptView,
    RunEvent,
    RunLifecycleImpact,
    RunLifecycleResult,
    RunMetrics,
    RunPage,
    RunRequestSnapshot,
    RunStatus,
    RunSummaryView,
    RunTrashState,
    RunView,
    StructuredRecoveryNotice,
)
from .database import (
    Base,
    DecisionRecord,
    PrimaryResearchCycleRecord,
    ResearchNodeRecord,
    RunArtifactRecord,
    RunAttemptRecord,
    RunEventRecord,
    RunEvidenceRecord,
    RunRecord,
    create_sqlite_engine,
)
from .errors import (
    IncrementalRequestConflictError,
    InvalidIncrementalBaselineError,
    InvalidResearchNodeComparisonError,
)
from .metrics import merge_run_metrics
from .recoveries import rebuild_structured_recoveries
from .reporting import order_reports
from .settings import AppSettings

_SECRET_RE = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|token)(\s*[:=]\s*)(\S+)"
)
_TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}

_DECISION_SECTION_KEYS = tuple(ResearchDecision.model_fields)


class InvalidPrimaryResearchCycleError(ValueError):
    """A requested Primary Research Cycle is not an active Full Cycle."""


def _numeric_audit_warning_message(
    appendix: DecisionNumericAuditAppendix | None,
) -> str:
    labels = tuple(
        item.reference_label or item.component_path
        for item in (appendix.omitted_components if appendix else ())
    )
    omitted = f": {', '.join(labels)}" if labels else ""
    return (
        "Optional numeric components were omitted because their audit failed"
        f"{omitted}. The qualitative decision remains audited."
    )


_SAFE_METRIC_KEYS = {
    "llm_calls",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "cache_hit_input_tokens",
    "cache_miss_input_tokens",
    "reasoning_output_tokens",
    "detailed_usage_calls",
    "wall_time_seconds",
}


def _utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None else None


def _sanitize_text(value: str | None, limit: int = 2000) -> str | None:
    if value is None:
        return None
    redacted = _SECRET_RE.sub(r"\1\2[REDACTED]", str(value))
    return redacted[:limit]


def _sanitize_payload(value: Any, key: str = "") -> Any:
    if key in _SAFE_METRIC_KEYS and isinstance(value, int | float):
        return value
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


class ArtifactConflictError(RuntimeError):
    pass


class EvidenceConflictError(RuntimeError):
    pass


class EvidenceNotSealedError(RuntimeError):
    pass


class RunRepository:
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

    def create_run(
        self,
        request: AnalysisRequest,
        config_snapshot: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        source_run_id: str | None = None,
        research_schema_version: str | None = None,
        information_cutoff_at: datetime | None = None,
        method_snapshot: dict[str, Any] | None = None,
        research_kind: str | None = None,
        full_baseline_run_id: str | None = None,
        incremental_input_fingerprint: str | None = None,
    ) -> tuple[RunView, bool]:
        if not isinstance(request, AnalysisRequest):
            raise TypeError("new Runs require an AnalysisRequest creation contract")
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
                        ):
                            raise IdempotencyConflictError(
                                "idempotency key was already used for a different request"
                            )
                        return self._view_for_session(session, existing), False
                if research_kind == "incremental":
                    if full_baseline_run_id is None or incremental_input_fingerprint is None:
                        raise ValueError(
                            "Incremental Research requires immutable baseline and fingerprint"
                        )
                    existing_slot = self._active_incremental_slot(
                        session,
                        full_baseline_run_id,
                        request.analysis_date,
                    )
                    if existing_slot is not None:
                        if (
                            existing_slot.incremental_input_fingerprint
                            == incremental_input_fingerprint
                        ):
                            return self._view_for_session(session, existing_slot), False
                        raise IncrementalRequestConflictError(
                            "An active Incremental Research Run already occupies this Cycle and cutoff."
                        )
                if (
                    research_kind == "full"
                    and request.make_primary is None
                    and session.get(PrimaryResearchCycleRecord, request.ticker) is not None
                ):
                    raise ValueError("later Full Research requires an explicit make_primary choice")
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
                    status=RunStatus.QUEUED.value,
                    request_json=request_json,
                    config_json=_sanitize_payload(config_snapshot),
                    research_schema_version=research_schema_version,
                    information_cutoff_at=(
                        information_cutoff_at.astimezone(UTC).replace(tzinfo=None)
                        if information_cutoff_at and information_cutoff_at.tzinfo
                        else information_cutoff_at
                    ),
                    method_snapshot_json=_sanitize_payload(method_snapshot)
                    if method_snapshot is not None
                    else None,
                    research_kind=research_kind,
                    full_baseline_run_id=full_baseline_run_id,
                    incremental_cutoff=(
                        request.analysis_date if research_kind == "incremental" else None
                    ),
                    incremental_input_fingerprint=incremental_input_fingerprint,
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
            if research_kind == "incremental":
                with self.sessions() as session:
                    existing_slot = self._active_incremental_slot(
                        session,
                        full_baseline_run_id,
                        request.analysis_date,
                    )
                    if existing_slot is not None:
                        if (
                            existing_slot.incremental_input_fingerprint
                            == incremental_input_fingerprint
                        ):
                            return self._view_for_session(session, existing_slot), False
                        raise IncrementalRequestConflictError(
                            "An active Incremental Research Run already occupies this Cycle and cutoff."
                        ) from exc
            if idempotency_key is None:
                raise
            with self.sessions() as session:
                existing = session.scalar(
                    select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                )
                if existing is None:
                    raise
                if existing.request_json != request_json or existing.source_run_id != source_run_id:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    ) from exc
                return self._view_for_session(session, existing), False
        return self.get_run(run_id), True

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

    @staticmethod
    def checkpoint_thread_id(run_id: str, attempt: int) -> str:
        return f"run:{run_id}:attempt:{attempt}"

    def get_run(self, run_id: str) -> RunView:
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            return self._view_for_session(session, record)

    def list_runs(
        self,
        *,
        trash_state: RunTrashState = RunTrashState.ACTIVE,
        status: RunStatus | None = None,
        research_kind: Literal["full", "incremental"] | None = None,
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
        if research_kind == "incremental":
            filters.append(RunRecord.research_kind == "incremental")
        elif research_kind == "full":
            filters.append(
                or_(RunRecord.research_kind == "full", RunRecord.research_kind.is_(None))
            )
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
            select(
                RunRecord,
                DecisionRecord.rating,
                DecisionRecord.confidence,
                ResearchNodeRecord.run_id,
            )
            .outerjoin(DecisionRecord, DecisionRecord.run_id == RunRecord.id)
            .outerjoin(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
            .where(*filters)
            .order_by(RunRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        count_stmt = select(func.count()).select_from(RunRecord).where(*filters)
        with self.sessions() as session:
            return RunPage(
                items=tuple(
                    self._summary(
                        record,
                        rating,
                        confidence,
                        node_run_id is not None,
                        instrument_name=self._effective_instrument_names(
                            session,
                            record,
                        )[0],
                        instrument_local_name=self._effective_instrument_names(
                            session,
                            record,
                        )[1],
                    )
                    for record, rating, confidence, node_run_id in session.execute(stmt)
                ),
                total=int(session.scalar(count_stmt) or 0),
                limit=limit,
                offset=offset,
            )

    def trash_runs(
        self,
        run_ids: tuple[str, ...],
        *,
        primary_replacements: dict[str, str] | None = None,
    ) -> tuple[tuple[RunView, ...], int]:
        """Compatibility wrapper for the Cycle-aware lifecycle result."""
        result = self.trash_runs_detailed(
            run_ids,
            primary_replacements=primary_replacements,
        )
        return result.runs, result.changed

    def trash_runs_detailed(
        self,
        run_ids: tuple[str, ...],
        *,
        primary_replacements: dict[str, str] | None = None,
    ) -> RunLifecycleResult:
        """Atomically Trash requested Runs and any Full-owned active Cycle."""
        now = _utc_naive()
        replacements = primary_replacements or {}
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
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
            requested_nodes = {
                node.run_id: node
                for node in session.scalars(
                    select(ResearchNodeRecord).where(ResearchNodeRecord.run_id.in_(run_ids))
                )
            }
            full_ids = tuple(
                run_id for run_id, node in requested_nodes.items() if node.research_kind == "full"
            )
            cycle_children = {
                full_id: tuple(
                    session.scalars(
                        select(RunRecord)
                        .join(
                            ResearchNodeRecord,
                            ResearchNodeRecord.run_id == RunRecord.id,
                        )
                        .where(
                            ResearchNodeRecord.full_baseline_run_id == full_id,
                            RunRecord.trashed_at.is_(None),
                        )
                        .order_by(RunRecord.id)
                    )
                )
                for full_id in full_ids
            }
            affected_ids = set(run_ids)
            for children in cycle_children.values():
                affected_ids.update(child.id for child in children)

            for primary in tuple(
                session.scalars(
                    select(PrimaryResearchCycleRecord).where(
                        PrimaryResearchCycleRecord.full_run_id.in_(full_ids)
                    )
                )
            ):
                remaining = tuple(
                    session.scalars(
                        select(ResearchNodeRecord.run_id)
                        .join(RunRecord, RunRecord.id == ResearchNodeRecord.run_id)
                        .where(
                            ResearchNodeRecord.research_kind == "full",
                            func.json_extract(RunRecord.request_json, "$.ticker")
                            == primary.instrument,
                            RunRecord.status == RunStatus.SUCCEEDED.value,
                            RunRecord.trashed_at.is_(None),
                            ResearchNodeRecord.run_id.not_in(affected_ids),
                        )
                        .order_by(ResearchNodeRecord.run_id)
                    )
                )
                replacement = replacements.get(primary.full_run_id)
                if remaining:
                    if replacement is None:
                        raise InvalidRunTransitionError(
                            "trashing the Primary Full requires an explicit replacement"
                        )
                    if replacement not in remaining:
                        raise InvalidRunTransitionError(
                            "replacement Primary must be an active Full Cycle on this Timeline"
                        )
                    primary.full_run_id = replacement
                    primary.updated_at = now
                else:
                    session.delete(primary)

            changed_ids: set[str] = set()
            for run_id in run_ids:
                record = records[run_id]
                if record.trashed_at is None:
                    record.trashed_at = now
                    record.trash_cascade_full_run_id = None
                    record.updated_at = now
                    changed_ids.add(run_id)
            for full_id, children in cycle_children.items():
                for child in children:
                    if child.trashed_at is None:
                        child.trashed_at = now
                        child.trash_cascade_full_run_id = full_id
                        child.updated_at = now
                        changed_ids.add(child.id)
            session.flush()
            views = tuple(
                self._view(
                    records[run_id],
                    is_research_node=run_id in requested_nodes,
                )
                for run_id in run_ids
            )
            impacts = tuple(
                RunLifecycleImpact(
                    requested_run_id=run_id,
                    cycle_id=(
                        run_id
                        if requested_nodes.get(run_id)
                        and requested_nodes[run_id].research_kind == "full"
                        else (
                            requested_nodes[run_id].full_baseline_run_id
                            if requested_nodes.get(run_id)
                            else None
                        )
                    ),
                    research_kind=(
                        requested_nodes[run_id].research_kind
                        if requested_nodes.get(run_id)
                        else None
                    ),
                    affected_run_ids=(
                        (run_id, *(child.id for child in cycle_children.get(run_id, ())))
                        if run_id in full_ids
                        else (run_id,)
                    ),
                    cascade_moved_run_ids=tuple(
                        child.id
                        for child in cycle_children.get(run_id, ())
                        if child.id in changed_ids
                    ),
                    replacement_primary_cycle_id=replacements.get(run_id),
                )
                for run_id in run_ids
            )
        return RunLifecycleResult(
            runs=views,
            changed=len(changed_ids),
            impacts=impacts,
        )

    def restore_runs(
        self,
        run_ids: tuple[str, ...],
    ) -> tuple[tuple[RunView, ...], int]:
        """Compatibility wrapper for the Cycle-aware lifecycle result."""
        result = self.restore_runs_detailed(run_ids)
        return result.runs, result.changed

    def restore_runs_detailed(
        self,
        run_ids: tuple[str, ...],
    ) -> RunLifecycleResult:
        """Restore requested Nodes without violating Full-Cycle invariants."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            records = {
                record.id: record
                for record in session.scalars(select(RunRecord).where(RunRecord.id.in_(run_ids)))
            }
            missing = [run_id for run_id in run_ids if run_id not in records]
            if missing:
                raise RunNotFoundError(", ".join(missing))
            requested_nodes = {
                node.run_id: node
                for node in session.scalars(
                    select(ResearchNodeRecord).where(ResearchNodeRecord.run_id.in_(run_ids))
                )
            }
            full_ids = tuple(
                run_id
                for run_id, node in requested_nodes.items()
                if node.research_kind == "full" and records[run_id].trashed_at is not None
            )
            cascade_children = {
                full_id: tuple(
                    session.scalars(
                        select(RunRecord)
                        .where(RunRecord.trash_cascade_full_run_id == full_id)
                        .order_by(RunRecord.id)
                    )
                )
                for full_id in full_ids
            }
            restore_ids = {run_id for run_id in run_ids if records[run_id].trashed_at is not None}
            for children in cascade_children.values():
                restore_ids.update(child.id for child in children)

            for full_id in full_ids:
                full = records[full_id]
                if (
                    full.status != RunStatus.SUCCEEDED.value
                    or full.research_schema_version != CURRENT_RESEARCH_SCHEMA_VERSION
                ):
                    raise InvalidRunTransitionError(
                        "restored Full must remain a valid current Full Baseline"
                    )

            restoring_nodes = {
                node.run_id: node
                for node in session.scalars(
                    select(ResearchNodeRecord).where(ResearchNodeRecord.run_id.in_(restore_ids))
                )
            }
            restoring_slots: set[tuple[str, date]] = set()
            for run_id, node in restoring_nodes.items():
                if node.research_kind != "incremental":
                    continue
                baseline = session.get(RunRecord, node.full_baseline_run_id)
                baseline_node = session.get(ResearchNodeRecord, node.full_baseline_run_id)
                if baseline is None or (
                    baseline.trashed_at is not None and baseline.id not in restore_ids
                ):
                    raise InvalidRunTransitionError(
                        "an Incremental cannot be restored while its Full remains in Trash"
                    )
                run = session.get(RunRecord, run_id)
                baseline_request = RunRequestSnapshot.model_validate(baseline.request_json)
                incremental_request = RunRequestSnapshot.model_validate(run.request_json)
                if (
                    baseline_node is None
                    or baseline_node.research_kind != "full"
                    or baseline.status != RunStatus.SUCCEEDED.value
                    or baseline.research_schema_version != CURRENT_RESEARCH_SCHEMA_VERSION
                    or baseline_request.ticker != incremental_request.ticker
                    or baseline_request.analysis_date >= incremental_request.analysis_date
                ):
                    raise InvalidRunTransitionError(
                        "restored Incremental must retain a valid current Full Baseline"
                    )
                slot = (node.full_baseline_run_id, run.incremental_cutoff)
                if slot in restoring_slots:
                    raise InvalidRunTransitionError(
                        "restore contains duplicate same-Cycle/cutoff slots"
                    )
                restoring_slots.add(slot)
                conflict = session.scalar(
                    select(RunRecord.id).where(
                        RunRecord.research_kind == "incremental",
                        RunRecord.full_baseline_run_id == node.full_baseline_run_id,
                        RunRecord.incremental_cutoff == run.incremental_cutoff,
                        RunRecord.trashed_at.is_(None),
                        RunRecord.status.in_(
                            (
                                RunStatus.QUEUED.value,
                                RunStatus.RUNNING.value,
                                RunStatus.SUCCEEDED.value,
                            )
                        ),
                        RunRecord.id.not_in(restore_ids),
                    )
                )
                if conflict is not None:
                    raise InvalidRunTransitionError(
                        "restore conflicts with an active slot for the same Cycle/cutoff"
                    )

            fulls_by_instrument: dict[str, list[str]] = {}
            for full_id in full_ids:
                instrument = RunRequestSnapshot.model_validate(records[full_id].request_json).ticker
                fulls_by_instrument.setdefault(instrument, []).append(full_id)
            for full_id in full_ids:
                full = records[full_id]
                instrument = RunRequestSnapshot.model_validate(full.request_json).ticker
                primary = session.get(PrimaryResearchCycleRecord, instrument)
                active_other = session.scalar(
                    select(ResearchNodeRecord.run_id)
                    .join(RunRecord, RunRecord.id == ResearchNodeRecord.run_id)
                    .where(
                        ResearchNodeRecord.research_kind == "full",
                        ResearchNodeRecord.run_id != full_id,
                        RunRecord.trashed_at.is_(None),
                        RunRecord.status == RunStatus.SUCCEEDED.value,
                        func.json_extract(RunRecord.request_json, "$.ticker") == instrument,
                    )
                    .limit(1)
                )
                if primary is None:
                    if len(fulls_by_instrument[instrument]) > 1:
                        raise InvalidRunTransitionError(
                            "restoring multiple Full Cycles requires an explicit Primary choice"
                        )
                    if active_other is not None:
                        raise InvalidRunTransitionError(
                            "Timeline with active Cycles must retain an explicit Primary"
                        )
                    session.add(
                        PrimaryResearchCycleRecord(
                            instrument=instrument,
                            full_run_id=full_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )

            changed_ids: set[str] = set()
            for run_id in restore_ids:
                record = session.get(RunRecord, run_id)
                if record is not None and record.trashed_at is not None:
                    record.trashed_at = None
                    record.trash_cascade_full_run_id = None
                    record.updated_at = now
                    changed_ids.add(run_id)
            session.flush()
            views = tuple(
                self._view(
                    records[run_id],
                    is_research_node=run_id in requested_nodes,
                )
                for run_id in run_ids
            )
            impacts = tuple(
                RunLifecycleImpact(
                    requested_run_id=run_id,
                    cycle_id=(
                        run_id
                        if requested_nodes.get(run_id)
                        and requested_nodes[run_id].research_kind == "full"
                        else (
                            requested_nodes[run_id].full_baseline_run_id
                            if requested_nodes.get(run_id)
                            else None
                        )
                    ),
                    research_kind=(
                        requested_nodes[run_id].research_kind
                        if requested_nodes.get(run_id)
                        else None
                    ),
                    affected_run_ids=(
                        (run_id, *(child.id for child in cascade_children.get(run_id, ())))
                        if run_id in full_ids
                        else (run_id,)
                    ),
                    cascade_moved_run_ids=tuple(
                        child.id for child in cascade_children.get(run_id, ())
                    ),
                )
                for run_id in run_ids
            )
        return RunLifecycleResult(
            runs=views,
            changed=len(changed_ids),
            impacts=impacts,
        )

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
        asset_type = func.coalesce(
            func.json_extract(RunRecord.request_json, "$.asset_type"),
            "stock",
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
                asset_type == "stock",
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

    def active_run_counts(self) -> dict[str, int]:
        """Count current stock Runs by lifecycle status for health reporting."""
        asset_type = func.coalesce(
            func.json_extract(RunRecord.request_json, "$.asset_type"),
            "stock",
        )
        stmt = (
            select(RunRecord.status, func.count())
            .where(
                RunRecord.trashed_at.is_(None),
                asset_type == "stock",
            )
            .group_by(RunRecord.status)
        )
        with self.sessions() as session:
            return {str(status): int(count) for status, count in session.execute(stmt)}

    def purge_expired_trash(
        self,
        *,
        cutoff: datetime,
        batch_size: int = 50,
    ) -> int:
        """Permanently remove one bounded batch of expired trashed runs.

        Checkpoint rows and application-owned rows are deleted in the same
        SQLite write transaction. A checkpoint failure therefore preserves the
        run and all of its application data for a later retry.
        """
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
                            RunRecord.trash_cascade_full_run_id.is_(None),
                        )
                        .order_by(RunRecord.trashed_at, RunRecord.id)
                        .limit(batch_size)
                    )
                )
                if not run_ids:
                    connection.commit()
                    return 0
                full_ids = set(
                    connection.scalars(
                        select(ResearchNodeRecord.run_id).where(
                            ResearchNodeRecord.run_id.in_(run_ids),
                            ResearchNodeRecord.research_kind == "full",
                        )
                    )
                )
                target_ids = set(run_ids)
                if full_ids:
                    target_ids.update(
                        connection.scalars(
                            select(ResearchNodeRecord.run_id).where(
                                ResearchNodeRecord.full_baseline_run_id.in_(full_ids)
                            )
                        )
                    )
                checkpoint_threads = tuple(
                    dict.fromkeys(
                        connection.scalars(
                            select(RunAttemptRecord.checkpoint_thread_id)
                            .where(RunAttemptRecord.run_id.in_(target_ids))
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
                child_ids = target_ids - full_ids
                deleted = 0
                if child_ids:
                    deleted += int(
                        connection.execute(
                            delete(RunRecord).where(
                                RunRecord.id.in_(child_ids),
                                RunRecord.trashed_at.is_not(None),
                            )
                        ).rowcount
                        or 0
                    )
                if full_ids:
                    deleted += int(
                        connection.execute(
                            delete(RunRecord).where(
                                RunRecord.id.in_(full_ids),
                                RunRecord.trashed_at.is_not(None),
                                RunRecord.trashed_at <= cutoff,
                            )
                        ).rowcount
                        or 0
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return int(deleted or 0)

    def purge_runs_detailed(
        self,
        run_ids: tuple[str, ...],
    ) -> RunLifecycleResult:
        """Permanently purge trashed Runs at their Node-owned boundaries."""
        runs_table = RunRecord.__table__
        nodes_table = ResearchNodeRecord.__table__
        attempts_table = RunAttemptRecord.__table__
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                requested = {
                    str(row["id"]): row
                    for row in connection.execute(
                        select(runs_table).where(runs_table.c.id.in_(run_ids))
                    ).mappings()
                }
                nodes = {
                    str(row["run_id"]): row
                    for row in connection.execute(
                        select(nodes_table).where(nodes_table.c.run_id.in_(run_ids))
                    ).mappings()
                }
                active = [run_id for run_id, row in requested.items() if row["trashed_at"] is None]
                if active:
                    raise InvalidRunTransitionError(
                        "only Runs in Trash can be permanently purged: " + ", ".join(active)
                    )
                affected_by_request: dict[str, tuple[str, ...]] = {}
                target_ids: set[str] = set()
                full_ids: set[str] = set()
                for run_id in run_ids:
                    row = requested.get(run_id)
                    if row is None:
                        affected_by_request[run_id] = ()
                        continue
                    node = nodes.get(run_id)
                    if node is not None and node["research_kind"] == "full":
                        full_ids.add(run_id)
                        children = tuple(
                            str(value)
                            for value in connection.scalars(
                                select(nodes_table.c.run_id)
                                .where(nodes_table.c.full_baseline_run_id == run_id)
                                .order_by(nodes_table.c.run_id)
                            )
                        )
                        cycle_ids = (run_id, *children)
                        retained_active = tuple(
                            connection.scalars(
                                select(runs_table.c.id).where(
                                    runs_table.c.id.in_(cycle_ids),
                                    runs_table.c.trashed_at.is_(None),
                                )
                            )
                        )
                        if retained_active:
                            raise InvalidRunTransitionError(
                                "a Full Cycle must be entirely in Trash before purge"
                            )
                        affected_by_request[run_id] = cycle_ids
                        target_ids.update(cycle_ids)
                    else:
                        affected_by_request[run_id] = (run_id,)
                        target_ids.add(run_id)

                checkpoint_threads = tuple(
                    dict.fromkeys(
                        connection.scalars(
                            select(attempts_table.c.checkpoint_thread_id)
                            .where(attempts_table.c.run_id.in_(target_ids))
                            .order_by(attempts_table.c.id)
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
                child_ids = target_ids - full_ids
                deleted = 0
                if child_ids:
                    deleted += int(
                        connection.execute(
                            delete(runs_table).where(runs_table.c.id.in_(child_ids))
                        ).rowcount
                        or 0
                    )
                if full_ids:
                    deleted += int(
                        connection.execute(
                            delete(runs_table).where(runs_table.c.id.in_(full_ids))
                        ).rowcount
                        or 0
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return RunLifecycleResult(
            changed=deleted,
            impacts=tuple(
                RunLifecycleImpact(
                    requested_run_id=run_id,
                    cycle_id=(
                        run_id
                        if nodes.get(run_id) and nodes[run_id]["research_kind"] == "full"
                        else (nodes[run_id]["full_baseline_run_id"] if nodes.get(run_id) else None)
                    ),
                    research_kind=(nodes[run_id]["research_kind"] if nodes.get(run_id) else None),
                    affected_run_ids=affected_by_request[run_id],
                )
                for run_id in run_ids
            ),
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
        slot_identity: tuple[str, Any] | None = None
        fingerprint: str | None = None
        try:
            with self.sessions.begin() as session:
                record = session.get(RunRecord, run_id)
                if record is None:
                    raise RunNotFoundError(run_id)
                self._require_retryable(record)
                if (
                    record.research_kind == "incremental"
                    and record.full_baseline_run_id is not None
                    and record.incremental_cutoff is not None
                ):
                    slot_identity = (
                        record.full_baseline_run_id,
                        record.incremental_cutoff,
                    )
                    fingerprint = record.incremental_input_fingerprint
                    existing_slot = self._active_incremental_slot(
                        session,
                        *slot_identity,
                    )
                    if existing_slot is not None:
                        if existing_slot.incremental_input_fingerprint == fingerprint:
                            return self._view_for_session(session, existing_slot)
                        raise IncrementalRequestConflictError(
                            "An active Incremental Research Run already occupies "
                            "this Cycle and cutoff."
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
        except IntegrityError as exc:
            if slot_identity is None:
                raise
            with self.sessions() as session:
                existing_slot = self._active_incremental_slot(
                    session,
                    *slot_identity,
                )
                if existing_slot is None:
                    raise
                if existing_slot.incremental_input_fingerprint == fingerprint:
                    return self._view_for_session(session, existing_slot)
            raise IncrementalRequestConflictError(
                "An active Incremental Research Run already occupies this Cycle and cutoff."
            ) from exc
        return self.get_run(run_id)

    def require_retryable(self, run_id: str) -> RunView:
        """Check the target lifecycle before any retry admission work."""
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            self._require_retryable(record)
            return self._view_for_session(session, record)

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
                    confidence=result.decision.confidence,
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

    def select_primary_cycle(
        self,
        instrument: str,
        full_run_id: str,
    ) -> ResearchTimeline:
        """Idempotently select one active Full Cycle for a Timeline."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.execute(
                select(RunRecord, ResearchNodeRecord)
                .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                .where(ResearchNodeRecord.run_id == full_run_id)
            ).one_or_none()
            if row is None:
                raise InvalidPrimaryResearchCycleError("Full Cycle was not found")
            run, node = row
            request = RunRequestSnapshot.model_validate(run.request_json)
            if (
                node.research_kind != "full"
                or request.ticker != instrument
                or run.status != RunStatus.SUCCEEDED.value
                or run.trashed_at is not None
            ):
                raise InvalidPrimaryResearchCycleError(
                    "Primary Research must be an active Full Cycle on this Timeline"
                )
            primary = session.get(PrimaryResearchCycleRecord, instrument)
            if primary is None:
                session.add(
                    PrimaryResearchCycleRecord(
                        instrument=instrument,
                        full_run_id=full_run_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            elif primary.full_run_id != full_run_id:
                primary.full_run_id = full_run_id
                primary.updated_at = now
        return self.get_timeline(instrument)

    def get_timeline(
        self,
        instrument: str,
        *,
        cycle_limit: int = 50,
        cycle_offset: int = 0,
        trash_state: RunTrashState | str = RunTrashState.ACTIVE,
    ) -> ResearchTimeline:
        """Return derived Cycles without copying Run products into a Timeline."""
        with self.sessions() as session:
            primary = session.get(PrimaryResearchCycleRecord, instrument)
            all_rows = list(
                session.execute(
                    select(RunRecord, ResearchNodeRecord)
                    .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                    .where(func.json_extract(RunRecord.request_json, "$.ticker") == instrument)
                    .order_by(
                        func.json_extract(RunRecord.request_json, "$.analysis_date"),
                        RunRecord.id,
                    )
                )
            )
            decision_records = list(
                session.execute(
                    select(DecisionRecord).where(
                        DecisionRecord.run_id.in_([run.id for run, _node in all_rows])
                    )
                ).scalars()
            )
        trash_state = RunTrashState(trash_state)
        full_rows = [(run, node) for run, node in all_rows if node.research_kind == "full"]
        increments_by_cycle: dict[str, list[tuple[RunRecord, ResearchNodeRecord]]] = {}
        for run, node in all_rows:
            if node.research_kind == "incremental" and node.full_baseline_run_id:
                increments_by_cycle.setdefault(node.full_baseline_run_id, []).append((run, node))
        visible_cycles = []
        for full_row in full_rows:
            full_run, _full_node = full_row
            increments = increments_by_cycle.get(full_run.id, [])
            if trash_state is RunTrashState.ACTIVE and full_run.trashed_at is not None:
                continue
            if trash_state is RunTrashState.TRASHED and not (
                full_run.trashed_at is not None
                or any(run.trashed_at is not None for run, _node in increments)
            ):
                continue
            visible_cycles.append(full_row)
        visible_cycles.sort(
            key=lambda row: (
                0 if primary is not None and row[0].id == primary.full_run_id else 1,
                -RunRequestSnapshot.model_validate(row[0].request_json).analysis_date.toordinal(),
                row[0].id,
            )
        )
        cycle_total = len(visible_cycles)
        page_full_rows = visible_cycles[cycle_offset : cycle_offset + cycle_limit]
        products_by_id = {
            run.id: (
                IncrementalNodeProducts.model_validate(node.incremental_products_json)
                if node.incremental_products_json is not None
                else None
            )
            for run, node in all_rows
        }
        decisions_by_id = {
            decision.run_id: ResearchDecision.model_validate(decision.decision_json)
            for decision in decision_records
        }
        cycle_warning_by_id = {
            full_run.id: any(
                other_run.trashed_at is None
                and other_node.full_baseline_run_id == full_run.id
                and bool(
                    products_by_id[other_run.id]
                    and products_by_id[other_run.id].full_research_required_reasons
                )
                for other_run, other_node in all_rows
            )
            for full_run, _full_node in full_rows
        }
        primary_warning = bool(primary and cycle_warning_by_id.get(primary.full_run_id))

        def hydrate_node(run: RunRecord, node: ResearchNodeRecord) -> ResearchNodeView:
            cycle_id = run.id if node.research_kind == "full" else node.full_baseline_run_id
            assert cycle_id is not None
            active_cycle_rows = [
                (other_run, other_node)
                for other_run, other_node in all_rows
                if other_run.trashed_at is None
                and (
                    other_run.id
                    if other_node.research_kind == "full"
                    else other_node.full_baseline_run_id
                )
                == cycle_id
            ]
            head_id = (
                max(
                    active_cycle_rows,
                    key=lambda row: (
                        RunRequestSnapshot.model_validate(row[0].request_json).analysis_date,
                        row[0].id,
                    ),
                )[0].id
                if active_cycle_rows
                else None
            )
            products = products_by_id[run.id]
            return ResearchNodeView(
                id=run.id,
                cycle_id=cycle_id,
                instrument=instrument,
                analysis_date=RunRequestSnapshot.model_validate(run.request_json).analysis_date,
                research_schema_version=run.research_schema_version,
                information_cutoff_at=_aware(run.information_cutoff_at),
                method_snapshot=run.method_snapshot_json or {},
                research_kind=node.research_kind,
                full_baseline_run_id=node.full_baseline_run_id,
                is_baseline_compatible=(
                    node.research_kind == "full"
                    and run.research_schema_version == CURRENT_RESEARCH_SCHEMA_VERSION
                ),
                is_cycle_head=run.id == head_id,
                is_primary=(primary is not None and primary.full_run_id == cycle_id),
                is_active=run.trashed_at is None,
                trashed_at=_aware(run.trashed_at),
                trash_cascade_full_run_id=run.trash_cascade_full_run_id,
                collection_summary=products.collection_summary if products else None,
                research_availability=products.research_availability if products else None,
                information_advancement=products.information_advancement if products else None,
                performance=products.performance if products else None,
                reassessment=products.reassessment if products else None,
                decision=decisions_by_id.get(run.id),
                full_research_required_reasons=(
                    products.full_research_required_reasons if products else ()
                ),
                cycle_warning=cycle_warning_by_id.get(cycle_id, False),
            )

        identity_rows = sorted(
            all_rows,
            key=lambda row: (
                0 if primary is not None and row[0].id == primary.full_run_id else 1,
                -RunRequestSnapshot.model_validate(row[0].request_json).analysis_date.toordinal(),
            ),
        )
        instrument_name = next(
            (run.instrument_name for run, _node in identity_rows if run.instrument_name),
            None,
        )
        instrument_local_name = next(
            (
                run.instrument_local_name
                for run, _node in identity_rows
                if run.instrument_local_name
            ),
            None,
        )
        return ResearchTimeline(
            instrument=instrument,
            instrument_name=instrument_name,
            instrument_local_name=instrument_local_name,
            primary_cycle_id=primary.full_run_id if primary else None,
            active_full_cycles=tuple(
                PrimaryCycleCandidate(
                    id=run.id,
                    analysis_date=RunRequestSnapshot.model_validate(run.request_json).analysis_date,
                    is_primary=bool(primary and primary.full_run_id == run.id),
                    rating=(decisions_by_id[run.id].rating if run.id in decisions_by_id else None),
                    confidence=(
                        decisions_by_id[run.id].confidence if run.id in decisions_by_id else None
                    ),
                )
                for run, _node in sorted(
                    ((run, node) for run, node in full_rows if run.trashed_at is None),
                    key=lambda row: (
                        0 if primary is not None and row[0].id == primary.full_run_id else 1,
                        -RunRequestSnapshot.model_validate(
                            row[0].request_json
                        ).analysis_date.toordinal(),
                        row[0].id,
                    ),
                )
            ),
            cycles=tuple(
                ResearchCycleView(
                    id=full_run.id,
                    is_primary=bool(primary and primary.full_run_id == full_run.id),
                    cycle_warning=cycle_warning_by_id.get(full_run.id, False),
                    head_run_id=max(
                        [
                            full_run,
                            *(run for run, _node in increments_by_cycle.get(full_run.id, [])),
                        ],
                        key=lambda candidate: (
                            candidate.trashed_at is None,
                            RunRequestSnapshot.model_validate(candidate.request_json).analysis_date,
                            candidate.id,
                        ),
                    ).id,
                    baseline=hydrate_node(full_run, full_node),
                    increments=tuple(
                        hydrate_node(run, node)
                        for run, node in sorted(
                            increments_by_cycle.get(full_run.id, []),
                            key=lambda row: (
                                RunRequestSnapshot.model_validate(
                                    row[0].request_json
                                ).analysis_date,
                                row[0].id,
                            ),
                        )
                        if trash_state is RunTrashState.ALL
                        or (trash_state is RunTrashState.ACTIVE and run.trashed_at is None)
                        or (trash_state is RunTrashState.TRASHED and run.trashed_at is not None)
                    ),
                )
                for full_run, full_node in page_full_rows
            ),
            cycle_total=cycle_total,
            cycle_limit=cycle_limit,
            cycle_offset=cycle_offset,
            timeline_warning=primary_warning,
        )

    def compare_research_nodes(
        self,
        instrument: str,
        selections: tuple[ResearchNodeComparisonSelection, ...],
    ) -> ResearchNodeComparison:
        """Compute an ordered two-Node comparison without durable side effects."""
        if len(selections) != 2:
            raise InvalidResearchNodeComparisonError(
                "Node Comparison requires exactly two Research Node IDs"
            )
        node_ids = tuple(selection.node_id for selection in selections)
        if len(set(node_ids)) != 2:
            raise InvalidResearchNodeComparisonError(
                "Node Comparison requires two distinct Research Node IDs"
            )
        with self.sessions() as session:
            rows = {
                run.id: (run, node)
                for run, node in session.execute(
                    select(RunRecord, ResearchNodeRecord)
                    .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                    .where(RunRecord.id.in_(node_ids))
                )
            }
            decisions = {
                record.run_id: dict(record.decision_json)
                for record in session.execute(
                    select(DecisionRecord).where(DecisionRecord.run_id.in_(node_ids))
                ).scalars()
            }
        if set(rows) != set(node_ids):
            raise InvalidResearchNodeComparisonError(
                "Every comparison side must be a retained Research Node"
            )

        sides: list[ResearchNodeComparisonSide] = []
        for selection in selections:
            run, node = rows[selection.node_id]
            request = RunRequestSnapshot.model_validate(run.request_json)
            if run.status != RunStatus.SUCCEEDED.value:
                raise InvalidResearchNodeComparisonError(
                    "Failed or cancelled Research Runs cannot be compared"
                )
            if request.ticker != instrument:
                raise InvalidResearchNodeComparisonError(
                    "Both Research Nodes must use the requested Instrument Key"
                )
            actual_lifecycle = (
                ResearchNodeLifecycleState.TRASHED
                if run.trashed_at is not None
                else ResearchNodeLifecycleState.ACTIVE
            )
            if selection.lifecycle_state is not actual_lifecycle:
                raise InvalidResearchNodeComparisonError(
                    "Trash participation must be selected explicitly"
                )
            decision = decisions.get(run.id)
            if decision is None:
                raise InvalidResearchNodeComparisonError(
                    "Every compared Research Node must retain its Decision"
                )
            products = (
                IncrementalNodeProducts.model_validate(node.incremental_products_json)
                if node.incremental_products_json is not None
                else None
            )
            cycle_id = run.id if node.research_kind == "full" else node.full_baseline_run_id
            assert cycle_id is not None
            sides.append(
                ResearchNodeComparisonSide(
                    node_id=run.id,
                    cycle_id=cycle_id,
                    analysis_date=request.analysis_date,
                    research_schema_version=run.research_schema_version,
                    method_snapshot=run.method_snapshot_json or {},
                    research_kind=node.research_kind,
                    lifecycle_state=actual_lifecycle,
                    collection_summary=products.collection_summary if products else None,
                    research_availability=products.research_availability if products else None,
                    information_advancement=products.information_advancement if products else None,
                    reassessment=products.reassessment if products else None,
                    decision=decision,
                    performance=products.performance if products else None,
                    full_research_required_reasons=(
                        products.full_research_required_reasons if products else ()
                    ),
                )
            )

        def comparison_value(decision: dict[str, Any], key: str):
            if key not in decision:
                return ResearchNodeComparisonValue(state="not_recorded_under_this_schema")
            value = decision[key]
            if value is None:
                return ResearchNodeComparisonValue(state="null")
            if value == "" or value == [] or value == {}:
                return ResearchNodeComparisonValue(state="empty", value=value)
            return ResearchNodeComparisonValue(state="recorded", value=value)

        method_changed = sides[0].method_snapshot != sides[1].method_snapshot
        return ResearchNodeComparison(
            instrument=instrument,
            sides=(sides[0], sides[1]),
            cross_cycle=sides[0].cycle_id != sides[1].cycle_id,
            method_changed=method_changed,
            warnings=(
                (
                    ResearchNodeComparisonWarning(
                        code="method_changed",
                        message=(
                            "Method Snapshots differ; conclusion differences are not "
                            "automatically attributable to Evidence, models, prompts, or methods."
                        ),
                    ),
                )
                if method_changed
                else ()
            ),
            decision_sections=tuple(
                ResearchNodeDecisionSection(
                    key=key,
                    values=(
                        comparison_value(sides[0].decision, key),
                        comparison_value(sides[1].decision, key),
                    ),
                )
                for key in _DECISION_SECTION_KEYS
            ),
        )

    def get_research_node(self, run_id: str) -> ResearchNodeView | None:
        """Return one Run-backed Research Node with its derived Cycle state."""
        with self.sessions() as session:
            row = session.execute(
                select(RunRecord, ResearchNodeRecord)
                .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                .where(RunRecord.id == run_id)
            ).one_or_none()
            if row is None:
                return None
            run, node = row
            request = RunRequestSnapshot.model_validate(run.request_json)
            cycle_id = run.id if node.research_kind == "full" else node.full_baseline_run_id
            assert cycle_id is not None
            cycle_rows = list(
                session.execute(
                    select(RunRecord, ResearchNodeRecord)
                    .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                    .where(
                        or_(
                            ResearchNodeRecord.run_id == cycle_id,
                            ResearchNodeRecord.full_baseline_run_id == cycle_id,
                        )
                    )
                )
            )
            primary = session.get(PrimaryResearchCycleRecord, request.ticker)
            decision_record = session.scalar(
                select(DecisionRecord).where(DecisionRecord.run_id == run_id)
            )
        active_rows = [item for item in cycle_rows if item[0].trashed_at is None]
        head_id = (
            max(
                active_rows,
                key=lambda item: (
                    RunRequestSnapshot.model_validate(item[0].request_json).analysis_date,
                    item[0].id,
                ),
            )[0].id
            if active_rows
            else None
        )
        products = (
            IncrementalNodeProducts.model_validate(node.incremental_products_json)
            if node.incremental_products_json
            else None
        )
        cycle_warning = any(
            other_run.trashed_at is None
            and other_node.research_kind == "incremental"
            and bool(
                other_node.incremental_products_json
                and IncrementalNodeProducts.model_validate(
                    other_node.incremental_products_json
                ).full_research_required_reasons
            )
            for other_run, other_node in cycle_rows
        )
        return ResearchNodeView(
            id=run.id,
            cycle_id=cycle_id,
            instrument=request.ticker,
            analysis_date=request.analysis_date,
            research_schema_version=run.research_schema_version,
            information_cutoff_at=_aware(run.information_cutoff_at),
            method_snapshot=run.method_snapshot_json or {},
            research_kind=node.research_kind,
            full_baseline_run_id=node.full_baseline_run_id,
            is_baseline_compatible=(
                node.research_kind == "full"
                and run.research_schema_version == CURRENT_RESEARCH_SCHEMA_VERSION
            ),
            is_cycle_head=run.id == head_id,
            is_primary=bool(primary and primary.full_run_id == cycle_id),
            is_active=run.trashed_at is None,
            trashed_at=_aware(run.trashed_at),
            trash_cascade_full_run_id=run.trash_cascade_full_run_id,
            collection_summary=products.collection_summary if products else None,
            research_availability=products.research_availability if products else None,
            information_advancement=products.information_advancement if products else None,
            performance=products.performance if products else None,
            reassessment=products.reassessment if products else None,
            decision=(
                ResearchDecision.model_validate(decision_record.decision_json)
                if decision_record
                else None
            ),
            full_research_required_reasons=(
                products.full_research_required_reasons if products else ()
            ),
            cycle_warning=cycle_warning,
        )

    def get_incremental_context(self, run_id: str) -> IncrementalRunContext | None:
        """Read the Incremental brief and Full Decision without loading artifacts."""

        context = self._incremental_context_records(run_id, include_evidence=False)
        if context is None:
            return None
        products, baseline_run, baseline_decision, _baseline_evidence = context
        baseline_request = RunRequestSnapshot.model_validate(baseline_run.request_json)
        return IncrementalRunContext(
            analysis_brief=products.analysis_brief if products else None,
            full_baseline=IncrementalBaselineContext(
                run_id=baseline_run.id,
                analysis_date=baseline_request.analysis_date,
                decision=ResearchDecision.model_validate(baseline_decision.decision_json),
            ),
        )

    def get_incremental_export_context(
        self,
        run_id: str,
    ) -> IncrementalExportContext | None:
        """Return the self-contained baseline context required by export schema v11."""

        context = self._incremental_context_records(run_id, include_evidence=True)
        if context is None:
            return None
        products, baseline_run, baseline_decision, baseline_evidence = context
        assert baseline_evidence is not None
        baseline_request = RunRequestSnapshot.model_validate(baseline_run.request_json)
        return IncrementalExportContext(
            analysis_brief=products.analysis_brief if products else None,
            full_baseline=IncrementalBaselineContext(
                run_id=baseline_run.id,
                analysis_date=baseline_request.analysis_date,
                decision=ResearchDecision.model_validate(baseline_decision.decision_json),
            ),
            full_baseline_evidence=EvidenceBundle.model_validate(baseline_evidence.bundle_json),
        )

    def _incremental_context_records(
        self,
        run_id: str,
        *,
        include_evidence: bool,
    ) -> (
        tuple[
            IncrementalNodeProducts | None,
            RunRecord,
            DecisionRecord,
            RunEvidenceRecord | None,
        ]
        | None
    ):
        with self.sessions() as session:
            node = session.get(ResearchNodeRecord, run_id)
            if node is None or node.research_kind != "incremental":
                return None
            baseline_id = node.full_baseline_run_id
            assert baseline_id is not None
            baseline_run = session.get(RunRecord, baseline_id)
            baseline_decision = session.scalar(
                select(DecisionRecord).where(DecisionRecord.run_id == baseline_id)
            )
            baseline_evidence = (
                session.get(RunEvidenceRecord, baseline_id) if include_evidence else None
            )
            if baseline_run is None or baseline_decision is None:
                raise InvalidIncrementalBaselineError(
                    "Incremental Full Baseline context is incomplete"
                )
            if include_evidence and baseline_evidence is None:
                raise EvidenceNotSealedError(baseline_id)
            products = (
                IncrementalNodeProducts.model_validate(node.incremental_products_json)
                if node.incremental_products_json
                else None
            )
            return products, baseline_run, baseline_decision, baseline_evidence

    def list_timelines(self, *, limit: int = 50, offset: int = 0) -> ResearchTimelinePage:
        """List derived Timelines without introducing a second product store."""
        with self.sessions() as session:
            rows = list(
                session.execute(
                    select(RunRecord, ResearchNodeRecord)
                    .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                    .where(RunRecord.trashed_at.is_(None))
                )
            )
            primary_by_instrument = {
                record.instrument: record.full_run_id
                for record in session.scalars(select(PrimaryResearchCycleRecord))
            }
            decisions_by_id = {
                record.run_id: ResearchDecision.model_validate(record.decision_json)
                for record in session.scalars(
                    select(DecisionRecord).where(
                        DecisionRecord.run_id.in_([run.id for run, _node in rows])
                    )
                )
            }
        grouped: dict[str, list[tuple[RunRecord, ResearchNodeRecord]]] = {}
        for run, node in rows:
            ticker = RunRequestSnapshot.model_validate(run.request_json).ticker
            grouped.setdefault(ticker, []).append((run, node))
        summaries = []
        for instrument, instrument_rows in grouped.items():
            primary_id = primary_by_instrument.get(instrument)
            primary_rows = [
                (run, node)
                for run, node in instrument_rows
                if (run.id if node.research_kind == "full" else node.full_baseline_run_id)
                == primary_id
            ]
            primary_head = (
                max(
                    primary_rows,
                    key=lambda row: (
                        RunRequestSnapshot.model_validate(row[0].request_json).analysis_date,
                        row[0].id,
                    ),
                )[0]
                if primary_rows
                else None
            )
            primary_decision = decisions_by_id.get(primary_head.id) if primary_head else None
            identity_rows = sorted(
                instrument_rows,
                key=lambda row: (
                    0 if row[0].id == primary_id else 1,
                    -RunRequestSnapshot.model_validate(
                        row[0].request_json
                    ).analysis_date.toordinal(),
                ),
            )
            timeline_warning = any(
                node.research_kind == "incremental"
                and node.full_baseline_run_id == primary_id
                and bool(
                    node.incremental_products_json
                    and IncrementalNodeProducts.model_validate(
                        node.incremental_products_json
                    ).full_research_required_reasons
                )
                for _run, node in instrument_rows
            )
            summaries.append(
                ResearchTimelineSummary(
                    instrument=instrument,
                    instrument_name=next(
                        (
                            run.instrument_name
                            for run, _node in identity_rows
                            if run.instrument_name
                        ),
                        None,
                    ),
                    instrument_local_name=next(
                        (
                            run.instrument_local_name
                            for run, _node in identity_rows
                            if run.instrument_local_name
                        ),
                        None,
                    ),
                    primary_cycle_id=primary_id,
                    full_cycle_count=sum(
                        node.research_kind == "full" for _run, node in instrument_rows
                    ),
                    incremental_node_count=sum(
                        node.research_kind == "incremental" for _run, node in instrument_rows
                    ),
                    latest_analysis_date=max(
                        RunRequestSnapshot.model_validate(run.request_json).analysis_date
                        for run, _node in instrument_rows
                    ),
                    primary_rating=primary_decision.rating if primary_decision else None,
                    primary_confidence=(primary_decision.confidence if primary_decision else None),
                    timeline_warning=timeline_warning,
                )
            )
        summaries.sort(key=lambda item: (-item.latest_analysis_date.toordinal(), item.instrument))
        total = len(summaries)
        return ResearchTimelinePage(
            items=tuple(summaries[offset : offset + limit]),
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_full_baseline_candidates(
        self,
        instrument: str,
        *,
        before: date,
    ) -> tuple[FullBaselineCandidate, ...]:
        """Return active compatible Full Baselines without hydrating a Timeline page."""
        with self.sessions() as session:
            primary = session.get(PrimaryResearchCycleRecord, instrument)
            rows = list(
                session.execute(
                    select(RunRecord, ResearchNodeRecord, DecisionRecord)
                    .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                    .outerjoin(DecisionRecord, DecisionRecord.run_id == RunRecord.id)
                    .where(
                        func.json_extract(RunRecord.request_json, "$.ticker") == instrument,
                        ResearchNodeRecord.research_kind == "full",
                        RunRecord.status == RunStatus.SUCCEEDED.value,
                        RunRecord.trashed_at.is_(None),
                        RunRecord.research_schema_version == CURRENT_RESEARCH_SCHEMA_VERSION,
                        func.json_extract(RunRecord.request_json, "$.analysis_date")
                        < before.isoformat(),
                    )
                )
            )
            child_rows = list(
                session.execute(
                    select(RunRecord, ResearchNodeRecord)
                    .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                    .where(
                        ResearchNodeRecord.full_baseline_run_id.in_(
                            [run.id for run, _node, _decision in rows]
                        ),
                        RunRecord.trashed_at.is_(None),
                    )
                )
            )
        warned_cycles = {
            node.full_baseline_run_id
            for _run, node in child_rows
            if node.incremental_products_json
            and IncrementalNodeProducts.model_validate(
                node.incremental_products_json
            ).full_research_required_reasons
        }
        candidates = []
        for run, _node, decision_record in rows:
            request = RunRequestSnapshot.model_validate(run.request_json)
            decision = (
                ResearchDecision.model_validate(decision_record.decision_json)
                if decision_record
                else None
            )
            candidates.append(
                FullBaselineCandidate(
                    id=run.id,
                    analysis_date=request.analysis_date,
                    is_primary=bool(primary and primary.full_run_id == run.id),
                    instrument_name=run.instrument_name,
                    instrument_local_name=run.instrument_local_name,
                    rating=decision.rating if decision else None,
                    confidence=decision.confidence if decision else None,
                    thesis=decision.thesis if decision else None,
                    cycle_warning=run.id in warned_cycles,
                )
            )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    0 if item.is_primary else 1,
                    -item.analysis_date.toordinal(),
                    item.id,
                ),
            )
        )

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
    def market_bucket(ticker: str, asset_type: str | None = None) -> str | None:
        del asset_type  # retained for callers reading legacy decision rows
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
        confidence: float | None,
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
            research_confidence=confidence,
        )
