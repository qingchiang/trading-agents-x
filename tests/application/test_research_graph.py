from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest

from tradingagents.application.contracts import (
    AnalysisRequest,
    EvidenceQuality,
    PerspectiveReview,
    ResearchDecision,
    ResearchRating,
    RunProfile,
)
from tradingagents.application.metrics import MetricsCallback
from tradingagents.application.runtime import RunContext
from tradingagents.graph.research_graph import (
    ResearchGraph,
    _evidence_from_record,
)
from tradingagents.provenance import ProvenanceRecord


class _StructuredInvoker:
    def __init__(self, schema, calls):
        self.schema = schema
        self.calls = calls

    def invoke(self, prompt):
        self.calls.append((self.schema.__name__, prompt))
        refs = tuple(dict.fromkeys(re.findall(r"ev_[a-f0-9]{12}", prompt)))
        if self.schema is PerspectiveReview:
            return PerspectiveReview(
                role="fixture",
                thesis="Structured review grounded in the sealed evidence.",
                claim_rebuttals=("The opposing claim needs stronger support.",),
                evidence_refs=refs[:1],
                risks=("Evidence quality may deteriorate.",),
            )
        if self.schema is ResearchDecision:
            return ResearchDecision(
                rating=ResearchRating.HOLD,
                confidence=0.65,
                thesis="The available evidence supports a balanced conclusion.",
                evidence_refs=refs[:1],
                catalysts=("Evidence improves",),
                risks=("Evidence deteriorates",),
                invalidation_conditions=("The cited evidence is superseded",),
                time_horizon="6-12 months",
            )
        raise AssertionError(self.schema)


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema):
        return _StructuredInvoker(schema, self.calls)

    def invoke(self, _prompt):
        raise AssertionError("structured output should succeed")


class _AnalystSubgraph:
    _REPORT_KEYS = {
        "market": "market_report",
        "social": "sentiment_report",
        "news": "news_report",
        "fundamentals": "fundamentals_report",
    }

    def __init__(self, analyst):
        self.analyst = analyst

    def invoke(self, state, **_kwargs):
        return {
            **state,
            self._REPORT_KEYS[self.analyst]: (
                f"{self.analyst.title()} evidence is mixed. "
                "The conclusion should preserve uncertainty."
            ),
        }


def _context(
    app_settings,
    profile: RunProfile,
    analysts=("market", "news"),
) -> RunContext:
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
        profile=profile,
        analysts=analysts,
    )
    settings = app_settings.resolve_run(request)
    return RunContext(
        run_id=f"fixture-{profile.value}",
        request=request,
        settings=settings,
        dataflow_config=settings.dataflow_config(app_settings),
        past_context="",
        instrument_context="The instrument is NVDA.",
        cancel_requested=lambda: False,
    )


@pytest.mark.parametrize(
    ("profile", "required_nodes", "forbidden_nodes"),
    [
        (
            RunProfile.FAST,
            {"analyst.market", "analyst.news", "committee.final"},
            {"review.bull", "judge.research", "risk.review"},
        ),
        (
            RunProfile.STANDARD,
            {
                "review.bull",
                "review.bear",
                "judge.research",
                "risk.review",
                "committee.final",
            },
            {"risk.aggressive", "risk.conservative"},
        ),
        (
            RunProfile.DEEP,
            {
                "review.bull.rebuttal",
                "review.bear.rebuttal",
                "risk.aggressive",
                "risk.neutral",
                "risk.conservative",
                "committee.final",
            },
            {"risk.review"},
        ),
    ],
)
def test_profiles_share_contract_but_use_distinct_topologies(
    app_settings,
    monkeypatch,
    profile,
    required_nodes,
    forbidden_nodes,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    quick = _FakeLLM()
    deep = _FakeLLM()
    events: list[dict[str, Any]] = []
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=profile,
        selected_analysts=("market", "news"),
        metrics=MetricsCallback(),
    )

    execution = graph.execute(
        _context(app_settings, profile),
        checkpoint_thread_id=f"profile:{profile.value}",
        on_event=events.append,
    )

    completed = {
        event["node"]
        for event in events
        if event["event_type"] == "node.completed"
    }
    assert required_nodes <= completed
    assert not forbidden_nodes & completed
    assert set(execution.reports) == {"market", "news"}
    assert execution.evidence.digest
    assert execution.decision.rating is ResearchRating.HOLD
    valid_refs = {item.ref for item in execution.evidence.items}
    assert set(execution.decision.evidence_refs) <= valid_refs


def test_deep_debate_stops_when_rebuttals_add_no_new_information(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {
            analyst: _AnalystSubgraph(analyst)
            for analyst in self.selected_analysts
        },
    )
    llm = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=llm,
        deep_llm=llm,
        profile=RunProfile.DEEP,
        selected_analysts=("market",),
    )

    execution = graph.execute(
        _context(app_settings, RunProfile.DEEP, analysts=("market",)),
        checkpoint_thread_id="deep-stop",
    )

    assert execution.state["rebuttal_round"] <= 2


def test_future_dated_provenance_is_withheld_before_bundle_sealing() -> None:
    item = _evidence_from_record(
        ProvenanceRecord(
            evidence="Future filing",
            source="fixture",
            requested="2026-07-24",
            effective="2026-07-25",
            timing="vendor returned a future record",
        ),
        requested_date=date(2026, 7, 24),
        content="This future payload must not reach any agent.",
    )

    assert item.effective_date is None
    assert item.content is None
    assert item.quality is EvidenceQuality.UNAVAILABLE
    assert "future-dated evidence withheld" in item.provenance["timing"]
