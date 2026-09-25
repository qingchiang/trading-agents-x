"""Shared offline fixtures for service contracts."""

from __future__ import annotations

from datetime import date
from threading import Barrier, Lock

from tests.research_helpers import stub_run_llms
from tests.support.factories import analyst_report, research_decision
from tradingagents.application.service import AnalysisService
from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem
from tradingagents.persistence.repository import RunRepository
from tradingagents.research.full.state import GraphExecution


def _equity_resolver(ticker: str, *, data_context) -> dict[str, str]:
    return {"symbol": ticker, "quote_type": "EQUITY"}


def _execution(ticker: str) -> GraphExecution:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fixture evidence",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Fixture evidence.",
    )
    bundle = EvidenceBundle(
        instrument=ticker,
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    report = analyst_report(
        executive_summary="Fixture summary.",
        confidence=0.8,
        evidence_ref=item.ref,
        narrative="Fixture report.",
    )
    decision = research_decision(
        confidence="medium",
        thesis="Fixture thesis.",
        evidence_refs=(item.ref,),
    )
    return GraphExecution(
        state={},
        evidence=bundle,
        reports={"market": report},
        decision=decision,
    )


class _Graph:
    barrier: Barrier | None = None
    observed: list[tuple[str, str, str]] = []
    lock = Lock()
    error: Exception | None = None

    def __init__(self, **_kwargs):
        pass

    def execute(self, context, *, on_event, **_kwargs):
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        with self.lock:
            self.observed.append(
                (
                    context.request.ticker,
                    context.settings.deep_binding.connection.compatibility,
                    context.settings.output_language.value,
                )
            )
        on_event(
            {
                "event_type": "node.completed",
                "node": "fixture",
                "payload": {"api_key": "must-not-persist"},
            }
        )
        if self.error is not None:
            raise self.error
        return _execution(context.request.ticker)


def _service(
    app_settings,
    repository: RunRepository,
    graph_factory=_Graph,
) -> AnalysisService:
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=stub_run_llms,
        graph_factory=graph_factory,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
    )
