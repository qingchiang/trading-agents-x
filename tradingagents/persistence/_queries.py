"""Internal queries operations on a shared repository session."""
from __future__ import annotations

from typing import Literal

from sqlalchemy import func, or_, select

from tradingagents.domain.common import (
    RunStatus,
    RunTrashState,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.evidence import EvidenceBundle
from tradingagents.domain.history import (
    RecentInstrument,
    RunAttemptView,
    RunGroupPage,
    RunGroupView,
    RunPage,
    RunSummaryView,
)
from tradingagents.domain.reporting import order_reports
from tradingagents.domain.reports import (
    AnalystReport,
)
from tradingagents.domain.runs import (
    AnalysisResult,
    RunMetrics,
    RunView,
)
from tradingagents.persistence._repository_common import (
    RunNotFoundError,
    _aware,
)
from tradingagents.persistence.models import (
    DecisionRecord,
    PrimaryResearchCycleRecord,
    ResearchNodeRecord,
    RunAttemptRecord,
    RunEvidenceRecord,
    RunRecord,
)


class QueriesOperations:
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

    def list_run_groups(
        self,
        *,
        trash_state: RunTrashState = RunTrashState.ACTIVE,
        status: RunStatus | None = None,
        research_kind: Literal["full", "incremental"] | None = None,
        q: str | None = None,
        limit: int = 12,
        offset: int = 0,
    ) -> RunGroupPage:
        """Match tasks before paging complete groups, retaining baseline context."""
        with self.sessions() as session:
            rows = list(
                session.execute(
                    select(RunRecord, DecisionRecord.rating, DecisionRecord.confidence).outerjoin(
                        DecisionRecord, DecisionRecord.run_id == RunRecord.id
                    )
                )
            )
            nodes = {node.run_id: node for node in session.scalars(select(ResearchNodeRecord))}
            primary_ids = set(session.scalars(select(PrimaryResearchCycleRecord.full_run_id)))
            views = {
                run.id: self._summary(
                    run,
                    rating,
                    confidence,
                    run.id in nodes,
                    instrument_name=run.instrument_name,
                    instrument_local_name=run.instrument_local_name,
                )
                for run, rating, confidence in rows
            }
        groups: dict[str, list[RunSummaryView]] = {}
        query = (q or "").strip().casefold()

        def visible(run: RunSummaryView) -> bool:
            return trash_state is RunTrashState.ALL or (run.trashed_at is not None) == (
                trash_state is RunTrashState.TRASHED
            )

        def matches(run: RunSummaryView) -> bool:
            return (
                visible(run)
                and (status is None or run.status == status)
                and (research_kind is None or (run.research_kind or "full") == research_kind)
                and (
                    not query
                    or any(
                        query in value.casefold()
                        for value in (
                            run.id,
                            run.request.ticker,
                            run.instrument_name or "",
                            run.instrument_local_name or "",
                        )
                    )
                )
            )

        for run in views.values():
            node = nodes.get(run.id)
            root = run.id if node and node.research_kind == "full" else run.full_baseline_run_id
            key = root if root in nodes and nodes[root].research_kind == "full" else run.id
            groups.setdefault(key, []).append(run)
        items = []
        for key, members in groups.items():
            matched = sorted(
                (run for run in members if matches(run)),
                key=lambda run: (run.created_at, run.id),
                reverse=True,
            )
            if not matched:
                continue
            baseline = (
                views.get(key) if key in nodes and nodes[key].research_kind == "full" else None
            )
            shown = sorted(
                (run for run in members if visible(run) or run.id == key),
                key=lambda run: (run.id != key, run.request.analysis_date, run.id),
            )
            research = tuple(run for run in shown if run.is_research_node)
            related = tuple(run for run in shown if not run.is_research_node)
            items.append(
                (
                    max(run.created_at for run in matched),
                    RunGroupView(
                        id=key,
                        kind="cycle" if baseline else "standalone",
                        instrument=matched[0].request.ticker,
                        baseline=baseline,
                        research_runs=research,
                        related_tasks=related,
                        matched_run_ids=tuple(run.id for run in matched),
                        is_primary=key in primary_ids,
                        cycle_warning=any(
                            run.trashed_at is None
                            and bool(
                                nodes[run.id].incremental_products_json
                                and nodes[run.id].incremental_products_json.get(
                                    "full_research_required_reasons"
                                )
                            )
                            for run in research
                        ),
                        status_counts={
                            value.value: sum(run.status == value for run in shown if visible(run))
                            for value in RunStatus
                        },
                    ),
                )
            )
        items.sort(key=lambda pair: (pair[0], pair[1].id), reverse=True)
        return RunGroupPage(
            items=tuple(item for _, item in items[offset : offset + limit]),
            total=len(items),
            limit=limit,
            offset=offset,
        )

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

    def active_run_counts(self) -> dict[str, int]:
        """Count current stock Runs by lifecycle status for health reporting."""
        stmt = (
            select(RunRecord.status, func.count())
            .where(
                RunRecord.trashed_at.is_(None),
            )
            .group_by(RunRecord.status)
        )
        with self.sessions() as session:
            return {str(status): int(count) for status, count in session.execute(stmt)}

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
