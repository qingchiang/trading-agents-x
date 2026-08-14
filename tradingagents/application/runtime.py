"""Immutable runtime context injected into LangGraph nodes and tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .anchor_readiness import AnchorReadinessResult
from .contracts import (
    AnalysisRequest,
    EvidenceBundle,
    MemoryContext,
    ResearchArtifactDraft,
)
from .settings import RunSettings


def _discard_artifact(_artifact: ResearchArtifactDraft) -> None:
    """Default sink used by direct graph callers without persistence."""


def _discard_evidence(_evidence: EvidenceBundle) -> None:
    """Default sink used by direct graph callers without persistence."""


def _no_sealed_evidence() -> EvidenceBundle | None:
    """Default source used by direct graph callers without persistence."""

    return None


@dataclass(frozen=True)
class RunContext:
    run_id: str
    request: AnalysisRequest
    settings: RunSettings
    dataflow_config: Mapping[str, Any]
    memory: MemoryContext
    instrument_context: str
    cancel_requested: Callable[[], bool]
    information_frontier: datetime | None = None
    anchor_readiness: AnchorReadinessResult | None = None
    shutdown_requested: Callable[[], bool] = lambda: False
    artifact_writer: Callable[[ResearchArtifactDraft], None] = _discard_artifact
    evidence_writer: Callable[[EvidenceBundle], None] = _discard_evidence
    sealed_evidence_reader: Callable[[], EvidenceBundle | None] = _no_sealed_evidence


class RunCancelled(RuntimeError):
    """Raised at a node boundary after a cooperative cancellation request."""


class WorkerShutdown(RuntimeError):
    """Raised at a node boundary so a worker can preserve and release its run."""


def check_cancelled(context: RunContext) -> None:
    if context.cancel_requested():
        raise RunCancelled(f"run {context.run_id} was cancelled")
    if context.shutdown_requested():
        raise WorkerShutdown(f"worker shutdown interrupted run {context.run_id}")
