"""Deterministic bounded collection for Shadow Research Chain updates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from time import monotonic
from typing import Any

from langchain_core.messages import ToolMessage

from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.graph.research_graph import collect_evidence
from tradingagents.provenance import SourceWatermark, attach_source_watermarks

from .contracts import AnalysisRequest, EvidenceBundle, NodeMetrics, RunMetrics
from .evidence import extract_evidence_tables
from .research import (
    ClaimStanding,
    IncrementalGateResult,
    QuestionStatus,
    ResearchRevision,
    assess_deterministic_update,
)
from .runtime import RunCancelled


def _required_sources(baseline: ResearchRevision) -> set[str]:
    sources = {
        source
        for claim in baseline.current_state.claims
        if claim.standing is ClaimStanding.ACTIVE
        for source in claim.required_sources
    }
    sources.update(
        source
        for question in baseline.current_state.questions
        if question.status is QuestionStatus.OPEN
        for source in question.required_sources
    )
    return sources


def _unavailable_payload(sources: tuple[str, ...], start: str, end: str) -> str:
    return attach_source_watermarks(
        "",
        *(
            SourceWatermark(
                source=source,
                scanned_start=start,
                scanned_end=end,
                status="unavailable",
                limitations=("Bounded collection failed before coverage was established.",),
            )
            for source in sources
        ),
    )


def run_deterministic_incremental_gate(
    baseline: ResearchRevision,
    request: AnalysisRequest,
    _config: dict[str, Any],
    cancel_requested: Callable[[], bool],
    *,
    on_progress: Callable[[IncrementalGateResult], None] | None = None,
) -> IncrementalGateResult:
    """Collect source-owned Japanese changes without invoking a model."""

    started = monotonic()
    payloads: list[tuple[str, str]] = []
    attempted_sources: set[str] = set()
    tool_calls = 0
    required_sources = _required_sources(baseline)
    overlap_start = (baseline.cutoff - timedelta(days=30)).isoformat()
    cutoff = request.analysis_date.isoformat()

    def collect(name: str, call: Callable[[], str], unavailable_sources: tuple[str, ...]):
        nonlocal tool_calls
        if cancel_requested():
            raise RunCancelled
        tool_calls += 1
        try:
            payload = call()
        except Exception:
            payload = _unavailable_payload(unavailable_sources, overlap_start, cutoff)
        payloads.append((name, payload))
        attempted_sources.update(unavailable_sources)

    def assess() -> IncrementalGateResult:
        items = []
        for index, (name, payload) in enumerate(payloads):
            items.extend(
                collect_evidence(
                    (
                        ToolMessage(
                            content=payload,
                            name=name,
                            tool_call_id=f"incremental-{index}",
                        ),
                    ),
                    "",
                    requested_date=request.analysis_date,
                    analyst="incremental",
                )
            )
        unique_items = tuple({item.ref: item for item in items}.values())
        bundle = EvidenceBundle(
            instrument=request.ticker,
            analysis_date=request.analysis_date,
            items=unique_items,
            tables=extract_evidence_tables(unique_items),
        )
        elapsed = max(0.0, monotonic() - started)
        phase = NodeMetrics(tool_calls=tool_calls, wall_time_seconds=elapsed)
        metrics = RunMetrics(
            tool_calls=tool_calls,
            wall_time_seconds=elapsed,
            node_metrics={"research.incremental.collect": phase},
        )
        result = assess_deterministic_update(
            baseline.id,
            baseline,
            request,
            bundle,
            metrics=metrics,
        )
        if on_progress is not None:
            on_progress(result)
        return result

    def should_stop(result: IncrementalGateResult) -> bool:
        if result.escalation_reason is None:
            return False
        if result.escalation_reason.value != "coverage_incomplete":
            return True
        return bool(
            result.coverage is not None
            and any(
                domain.requirement.value == "required"
                and domain.source in attempted_sources
                and domain.status.value != "complete"
                for domain in result.coverage.domains
            )
        )

    collect(
        "get_news",
        lambda: route_to_vendor(
            "get_news",
            request.ticker,
            overlap_start,
            cutoff,
            _provenance=True,
        ),
        ("EDINET", "TDnet", "Google News"),
    )
    partial = assess()
    if should_stop(partial):
        return partial
    if "fundamentals" in request.analysts or "J-Quants fundamentals" in required_sources:
        collect(
            "get_fundamentals",
            lambda: route_to_vendor(
                "get_fundamentals",
                request.ticker,
                cutoff,
                _provenance=True,
            ),
            ("J-Quants fundamentals",),
        )
        partial = assess()
        if should_stop(partial):
            return partial
    if (
        "market" in request.analysts
        or "J-Quants adjusted OHLCV" in required_sources
        or baseline.current_state.market_reference_levels
    ):
        collect(
            "get_verified_market_snapshot",
            lambda: route_to_vendor(
                "get_verified_market_snapshot",
                request.ticker,
                cutoff,
                260,
                _provenance=True,
            ),
            ("J-Quants adjusted OHLCV",),
        )
    if cancel_requested():
        raise RunCancelled
    return assess()
