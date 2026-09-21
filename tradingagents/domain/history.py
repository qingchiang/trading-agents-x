"""History contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from tradingagents.domain.artifacts import ResearchArtifact
from tradingagents.domain.common import (
    FrozenModel,
    ResearchConfidenceLevel,
    ResearchRating,
    RunStatus,
)
from tradingagents.domain.evidence import EvidenceBundle
from tradingagents.domain.incremental import IncrementalExportContext
from tradingagents.domain.runs import AnalysisResult, RunMetrics, RunView
from tradingagents.domain.timeline import ResearchNodeView


class RunAttemptView(FrozenModel):
    """Observed execution usage and lifecycle for one retry attempt."""

    attempt: int = Field(ge=1)
    status: RunStatus
    resume_count: int = Field(default=0, ge=0)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None


class RunSummaryView(RunView):
    research_rating: ResearchRating | None = None
    research_confidence: ResearchConfidenceLevel | None = None


class RunPage(FrozenModel):
    items: tuple[RunSummaryView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class RunGroupView(FrozenModel):
    id: str
    kind: Literal["cycle", "standalone"]
    instrument: str
    baseline: RunSummaryView | None = None
    research_runs: tuple[RunSummaryView, ...] = ()
    related_tasks: tuple[RunSummaryView, ...] = ()
    matched_run_ids: tuple[str, ...] = ()
    is_primary: bool = False
    cycle_warning: bool = False
    status_counts: dict[str, int] = Field(default_factory=dict)


class RunGroupPage(FrozenModel):
    items: tuple[RunGroupView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class RecentInstrument(FrozenModel):
    ticker: str
    instrument_name: str | None = None
    instrument_local_name: str | None = None
    last_used_at: datetime


class RunExport(FrozenModel):
    """Versioned, self-contained durable run export."""

    schema_version: Literal["11"] = "11"
    run: RunView
    result: AnalysisResult
    research_node: ResearchNodeView | None = None
    evidence: EvidenceBundle | None = None
    artifacts: tuple[ResearchArtifact, ...] = ()
    attempts: tuple[RunAttemptView, ...] = ()
    incremental_context: IncrementalExportContext | None = None
