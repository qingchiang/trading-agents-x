"""Internal lifecycle operations on a shared repository session."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, func, select

from tradingagents.domain.common import (
    RunStatus,
)
from tradingagents.domain.runs import (
    RunRequestSnapshot,
    RunView,
)
from tradingagents.domain.timeline import (
    PrimaryCycleCandidate,
    ResearchTimeline,
    RunLifecycleImpact,
    RunLifecyclePreview,
    RunLifecycleResult,
)
from tradingagents.persistence._repository_common import (
    _TERMINAL_STATUSES,
    InvalidPrimaryResearchCycleError,
    InvalidRunTransitionError,
    RunNotFoundError,
    _check_lifecycle_scope,
    _lifecycle_scope,
    _utc_naive,
    _validate_restoration,
)
from tradingagents.persistence.models import (
    DecisionRecord,
    PrimaryResearchCycleRecord,
    ResearchNodeRecord,
    RunAttemptRecord,
    RunRecord,
)


class LifecycleOperations:
    def preview_lifecycle(
        self,
        action: Literal["trash", "restore", "purge"],
        run_ids: tuple[str, ...],
    ) -> RunLifecyclePreview:
        """Read lifecycle ownership without changing runs or checkpoints."""
        with self.sessions() as session:
            affected = _lifecycle_scope(session.connection(), action, run_ids)
            records = list(
                session.scalars(
                    select(RunRecord).where(RunRecord.id.in_(affected)).order_by(RunRecord.id)
                )
            )
            nodes = {
                node.run_id: node
                for node in session.scalars(
                    select(ResearchNodeRecord).where(ResearchNodeRecord.run_id.in_(affected))
                )
            }
            blocked = []
            if action != "purge" and not set(run_ids).issubset({record.id for record in records}):
                blocked.append("Some selected runs no longer exist.")
            for record in records:
                if (
                    action == "trash"
                    and record.trashed_at is None
                    and record.status not in _TERMINAL_STATUSES
                ):
                    blocked.append(
                        "Only completed, failed or cancelled tasks can be moved to Trash."
                    )
                if action == "purge" and record.trashed_at is None:
                    blocked.append(
                        "All affected research must be in Trash before permanent deletion."
                    )
            if action == "restore":
                try:
                    _validate_restoration(
                        session, {record.id for record in records if record.trashed_at is not None}
                    )
                except InvalidRunTransitionError as error:
                    blocked.append(str(error))
            replacements = {}
            if action == "trash":
                for primary in session.scalars(
                    select(PrimaryResearchCycleRecord).where(
                        PrimaryResearchCycleRecord.full_run_id.in_(affected)
                    )
                ):
                    candidates = list(
                        session.execute(
                            select(RunRecord, DecisionRecord.rating, DecisionRecord.confidence)
                            .join(ResearchNodeRecord, ResearchNodeRecord.run_id == RunRecord.id)
                            .outerjoin(DecisionRecord, DecisionRecord.run_id == RunRecord.id)
                            .where(
                                ResearchNodeRecord.research_kind == "full",
                                RunRecord.trashed_at.is_(None),
                                RunRecord.id.not_in(affected),
                                func.json_extract(RunRecord.request_json, "$.ticker")
                                == primary.instrument,
                            )
                            .order_by(RunRecord.created_at.desc())
                        )
                    )
                    if candidates:
                        replacements[primary.full_run_id] = tuple(
                            PrimaryCycleCandidate(
                                id=run.id,
                                analysis_date=RunRequestSnapshot.model_validate(
                                    run.request_json
                                ).analysis_date,
                                rating=rating,
                                confidence=confidence,
                            )
                            for run, rating, confidence in candidates
                        )
            return RunLifecyclePreview(
                action=action,
                affected_run_ids=affected,
                affected_runs=tuple(
                    self._view(record, is_research_node=record.id in nodes) for record in records
                ),
                blocked_reasons=tuple(dict.fromkeys(blocked)),
                primary_replacements=replacements,
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
        expected_affected_run_ids: tuple[str, ...] | None = None,
    ) -> RunLifecycleResult:
        """Atomically Trash requested Runs and any Full-owned active Cycle."""
        now = _utc_naive()
        replacements = primary_replacements or {}
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            _check_lifecycle_scope(
                session.connection(), "trash", run_ids, expected_affected_run_ids
            )
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
        *,
        expected_affected_run_ids: tuple[str, ...] | None = None,
    ) -> RunLifecycleResult:
        """Restore requested Nodes without violating Full-Cycle invariants."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            _check_lifecycle_scope(
                session.connection(), "restore", run_ids, expected_affected_run_ids
            )
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

            _validate_restoration(session, restore_ids)

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
        *,
        expected_affected_run_ids: tuple[str, ...] | None = None,
    ) -> RunLifecycleResult:
        """Permanently purge trashed Runs at their Node-owned boundaries."""
        runs_table = RunRecord.__table__
        nodes_table = ResearchNodeRecord.__table__
        attempts_table = RunAttemptRecord.__table__
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                _check_lifecycle_scope(connection, "purge", run_ids, expected_affected_run_ids)
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
