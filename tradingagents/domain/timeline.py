"""Timeline contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from tradingagents.domain.collection import (
    CollectionSummary,
    InformationAdvancement,
    ResearchAvailability,
)
from tradingagents.domain.common import (
    FrozenModel,
    ResearchConfidenceLevel,
    ResearchRating,
    _StableStrEnum,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.incremental import (
    FullResearchRequiredReason,
    IncrementalDecisionOutcome,
    ResearchReassessment,
)
from tradingagents.domain.performance import PerformanceObservation
from tradingagents.domain.runs import RunView


class ResearchNodeView(FrozenModel):
    """A Run-backed Node; it deliberately owns no duplicate research data."""

    id: str
    cycle_id: str
    instrument: str
    analysis_date: date
    research_schema_version: str
    information_cutoff_at: datetime
    method_snapshot: dict[str, Any]
    research_kind: Literal["full", "incremental"]
    full_baseline_run_id: str | None = None
    is_baseline_compatible: bool
    is_cycle_head: bool
    is_primary: bool
    is_active: bool
    trashed_at: datetime | None = None
    trash_cascade_full_run_id: str | None = None
    collection_summary: CollectionSummary | None = None
    research_availability: ResearchAvailability | None = None
    information_advancement: InformationAdvancement | None = None
    performance: PerformanceObservation | None = None
    reassessment: ResearchReassessment | None = None
    decision_outcome: IncrementalDecisionOutcome | None = None
    decision_outcome_reason: str | None = None
    decision: ResearchDecision | None = None
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()
    cycle_warning: bool = False


class RunLifecycleImpact(FrozenModel):
    """The exact Run and Cycle scope affected by one lifecycle request."""

    requested_run_id: str
    cycle_id: str | None = None
    research_kind: Literal["full", "incremental"] | None = None
    affected_run_ids: tuple[str, ...] = ()
    cascade_moved_run_ids: tuple[str, ...] = ()
    replacement_primary_cycle_id: str | None = None


class RunLifecycleResult(FrozenModel):
    runs: tuple[RunView, ...] = ()
    changed: int = Field(ge=0)
    impacts: tuple[RunLifecycleImpact, ...] = ()


class ResearchCycleView(FrozenModel):
    """One Full-rooted Research Cycle returned as an indivisible UI unit."""

    id: str
    is_primary: bool = False
    cycle_warning: bool = False
    head_run_id: str
    baseline: ResearchNodeView
    increments: tuple[ResearchNodeView, ...] = ()


class PrimaryCycleCandidate(FrozenModel):
    """One active Full Cycle eligible for explicit Primary replacement."""

    id: str
    analysis_date: date
    is_primary: bool = False
    rating: ResearchRating | None = None
    confidence: ResearchConfidenceLevel | None = None


class RunLifecyclePreview(FrozenModel):
    action: Literal["trash", "restore", "purge"]
    affected_run_ids: tuple[str, ...]
    affected_runs: tuple[RunView, ...]
    blocked_reasons: tuple[str, ...] = ()
    primary_replacements: dict[str, tuple[PrimaryCycleCandidate, ...]] = Field(default_factory=dict)


class ResearchTimeline(FrozenModel):
    instrument: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    primary_cycle_id: str | None = None
    active_full_cycles: tuple[PrimaryCycleCandidate, ...] = ()
    cycles: tuple[ResearchCycleView, ...] = ()
    cycle_total: int = Field(default=0, ge=0)
    cycle_limit: int = Field(default=50, ge=1, le=200)
    cycle_offset: int = Field(default=0, ge=0)
    timeline_warning: bool = False

    @property
    def all_nodes(self) -> tuple[ResearchNodeView, ...]:
        """Flatten the current Cycle page for application-level traversal."""
        return tuple(node for cycle in self.cycles for node in (cycle.baseline, *cycle.increments))


class ResearchTimelineSummary(FrozenModel):
    """Derived Timeline identity and stable, non-duplicated summary metadata."""

    instrument: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    primary_cycle_id: str | None = None
    full_cycle_count: int = Field(ge=1)
    incremental_node_count: int = Field(default=0, ge=0)
    latest_analysis_date: date
    primary_baseline_date: date | None = None
    primary_thesis: str | None = None
    latest_research_completed_at: datetime | None = None
    latest_completed_run_id: str | None = None
    latest_completed_cycle_id: str | None = None
    latest_completed_analysis_date: date | None = None
    primary_head_run_id: str | None = None
    primary_analysis_date: date | None = None
    primary_rating: ResearchRating | None = None
    primary_confidence: ResearchConfidenceLevel | None = None
    timeline_warning: bool = False


class FullBaselineCandidate(FrozenModel):
    """One active Full Baseline eligible before a requested analysis cutoff."""

    id: str
    analysis_date: date
    is_primary: bool = False
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    rating: ResearchRating | None = None
    confidence: ResearchConfidenceLevel | None = None
    thesis: str | None = None
    cycle_warning: bool = False


class ResearchTimelinePage(FrozenModel):
    items: tuple[ResearchTimelineSummary, ...] = ()
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class ResearchNodeLifecycleState(_StableStrEnum):
    """Expected retained lifecycle state for an explicit comparison selection."""

    ACTIVE = "active"
    TRASHED = "trashed"


class ResearchNodeComparisonSelection(FrozenModel):
    """One explicitly selected side of an on-demand Node Comparison."""

    node_id: str = Field(min_length=1, max_length=36)
    lifecycle_state: ResearchNodeLifecycleState = ResearchNodeLifecycleState.ACTIVE

    @field_validator("node_id", mode="before")
    @classmethod
    def normalize_node_id(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ComparisonValueState(_StableStrEnum):
    """Presentation-safe distinction between schema absence and stored values."""

    RECORDED = "recorded"
    NULL = "null"
    EMPTY = "empty"
    NOT_RECORDED_UNDER_THIS_SCHEMA = "not_recorded_under_this_schema"


class ResearchNodeComparisonValue(FrozenModel):
    state: ComparisonValueState
    value: Any = None


class ResearchNodeDecisionSection(FrozenModel):
    """One fixed Decision section aligned in requested side order."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    values: tuple[ResearchNodeComparisonValue, ...] = Field(min_length=2, max_length=2)


class ResearchNodeComparisonWarning(FrozenModel):
    code: Literal["method_changed"]
    message: str = Field(min_length=1)


class ResearchNodeComparisonSide(FrozenModel):
    """Immutable per-Node context; values are never normalized across sides."""

    node_id: str
    cycle_id: str
    analysis_date: date
    research_schema_version: str
    method_snapshot: dict[str, Any]
    research_kind: Literal["full", "incremental"]
    lifecycle_state: ResearchNodeLifecycleState
    collection_summary: CollectionSummary | None = None
    research_availability: ResearchAvailability | None = None
    information_advancement: InformationAdvancement | None = None
    reassessment: ResearchReassessment | None = None
    decision_outcome: IncrementalDecisionOutcome | None = None
    decision_outcome_reason: str | None = None
    decision: dict[str, Any]
    performance: PerformanceObservation | None = None
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()


class ResearchNodeComparison(FrozenModel):
    """Deterministic, read-only comparison of exactly two retained Nodes."""

    instrument: str
    sides: tuple[ResearchNodeComparisonSide, ...] = Field(min_length=2, max_length=2)
    cross_cycle: bool
    method_changed: bool
    warnings: tuple[ResearchNodeComparisonWarning, ...] = ()
    decision_sections: tuple[ResearchNodeDecisionSection, ...]
