"""Internal timelines operations on a shared repository session."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import func, or_, select

from tradingagents.domain.common import (
    CURRENT_RESEARCH_SCHEMA_VERSION,
    RunStatus,
    RunTrashState,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.errors import (
    InvalidIncrementalBaselineError,
    InvalidResearchNodeComparisonError,
)
from tradingagents.domain.evidence import EvidenceBundle
from tradingagents.domain.incremental import (
    IncrementalBaselineContext,
    IncrementalExportContext,
    IncrementalNodeProducts,
    IncrementalRunContext,
)
from tradingagents.domain.runs import (
    RunRequestSnapshot,
)
from tradingagents.domain.timeline import (
    FullBaselineCandidate,
    PrimaryCycleCandidate,
    ResearchCycleView,
    ResearchNodeComparison,
    ResearchNodeComparisonSelection,
    ResearchNodeComparisonSide,
    ResearchNodeComparisonValue,
    ResearchNodeComparisonWarning,
    ResearchNodeDecisionSection,
    ResearchNodeLifecycleState,
    ResearchNodeView,
    ResearchTimeline,
    ResearchTimelinePage,
    ResearchTimelineSummary,
)
from tradingagents.persistence._repository_common import (
    _DECISION_SECTION_KEYS,
    EvidenceNotSealedError,
    InvalidRunTransitionError,
    RunNotFoundError,
    _aware,
)
from tradingagents.persistence.models import (
    DecisionRecord,
    PrimaryResearchCycleRecord,
    ResearchNodeRecord,
    RunEvidenceRecord,
    RunRecord,
)


class TimelinesOperations:
    def get_timeline(
        self,
        instrument: str,
        *,
        cycle_limit: int = 50,
        cycle_offset: int = 0,
        focus_node_id: str | None = None,
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
        if focus_node_id is not None:
            focused = next((row for row in all_rows if row[0].id == focus_node_id), None)
            if focused is None:
                raise RunNotFoundError(
                    "Requested research node is not available for this instrument"
                )
            focus_run, focus_node = focused
            if trash_state is RunTrashState.ACTIVE and focus_run.trashed_at is not None:
                raise InvalidRunTransitionError(
                    "Requested research is in Trash; explicitly include Trash to read it"
                )
            if trash_state is RunTrashState.TRASHED and focus_run.trashed_at is None:
                raise InvalidRunTransitionError(
                    "Requested research is active; select the active research view"
                )
            cycle_id = (
                focus_run.id
                if focus_node.research_kind == "full"
                else focus_node.full_baseline_run_id
            )
            index = next((i for i, row in enumerate(visible_cycles) if row[0].id == cycle_id), None)
            if index is None:
                raise RunNotFoundError("Requested research cycle is unavailable")
            cycle_offset = index // cycle_limit * cycle_limit
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
                decision_outcome=products.decision_outcome if products else None,
                decision_outcome_reason=(products.decision_outcome_reason if products else None),
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
                    decision_outcome=products.decision_outcome if products else None,
                    decision_outcome_reason=(
                        products.decision_outcome_reason if products else None
                    ),
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
            decision_outcome=products.decision_outcome if products else None,
            decision_outcome_reason=(products.decision_outcome_reason if products else None),
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

    def list_timelines(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        warning_only: bool = False,
        sort: Literal["analysis_date", "recent_activity"] = "analysis_date",
    ) -> ResearchTimelinePage:
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
            baseline = next((run for run, _ in instrument_rows if run.id == primary_id), None)
            completed = [
                (run, node) for run, node in instrument_rows if run.finished_at is not None
            ]
            recent = (
                max(completed, key=lambda row: (row[0].finished_at, row[0].id))
                if completed
                else None
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
                    primary_baseline_date=RunRequestSnapshot.model_validate(
                        baseline.request_json
                    ).analysis_date
                    if baseline
                    else None,
                    primary_thesis=primary_decision.thesis if primary_decision else None,
                    latest_research_completed_at=_aware(recent[0].finished_at) if recent else None,
                    latest_completed_run_id=recent[0].id if recent else None,
                    latest_completed_cycle_id=(
                        recent[0].id
                        if recent[1].research_kind == "full"
                        else recent[1].full_baseline_run_id
                    )
                    if recent
                    else None,
                    latest_completed_analysis_date=RunRequestSnapshot.model_validate(
                        recent[0].request_json
                    ).analysis_date
                    if recent
                    else None,
                    primary_head_run_id=primary_head.id if primary_head else None,
                    primary_analysis_date=(
                        RunRequestSnapshot.model_validate(primary_head.request_json).analysis_date
                        if primary_head
                        else None
                    ),
                    primary_rating=primary_decision.rating if primary_decision else None,
                    primary_confidence=(primary_decision.confidence if primary_decision else None),
                    timeline_warning=timeline_warning,
                )
            )
        query = (q or "").strip().casefold()
        summaries = [
            item
            for item in summaries
            if (not warning_only or item.timeline_warning)
            and (
                not query
                or any(
                    query in value.casefold()
                    for value in (
                        item.instrument,
                        item.instrument_name or "",
                        item.instrument_local_name or "",
                    )
                )
            )
        ]
        summaries.sort(key=lambda item: (-item.latest_analysis_date.toordinal(), item.instrument))
        if sort == "recent_activity":
            summaries.sort(
                key=lambda item: (
                    item.latest_research_completed_at or datetime.min.replace(tzinfo=UTC),
                    item.instrument,
                ),
                reverse=True,
            )
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
