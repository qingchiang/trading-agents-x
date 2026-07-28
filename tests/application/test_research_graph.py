from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest

from tradingagents.application.contracts import (
    AnalysisRequest,
    EvidenceItem,
    EvidenceQuality,
    MemoryContext,
    MemoryOutcome,
    MemoryRecord,
    PerspectiveReview,
    ResearchArtifactDraft,
    ResearchDecision,
    ResearchRating,
    RunProfile,
)
from tradingagents.application.metrics import MetricsCallback
from tradingagents.application.runtime import RunContext
from tradingagents.graph.research_graph import (
    ResearchGraph,
    _adapt_analyst_report,
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
        memory_refs = tuple(
            dict.fromkeys(
                re.findall(
                    r"memory:[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
                    prompt,
                )
            )
        )
        if self.schema is PerspectiveReview:
            parsed = PerspectiveReview(
                role="fixture",
                thesis="Structured review grounded in the sealed evidence.",
                claim_rebuttals=("The opposing claim needs stronger support.",),
                evidence_refs=refs[-1:],
                risks=("Evidence quality may deteriorate.",),
            )
        elif self.schema is ResearchDecision:
            parsed = ResearchDecision(
                rating=ResearchRating.HOLD,
                confidence=0.65,
                thesis="The available evidence supports a balanced conclusion.",
                evidence_refs=refs[-1:],
                memory_refs=memory_refs[:1],
                catalysts=("Evidence improves",),
                risks=("Evidence deteriorates",),
                invalidation_conditions=("The cited evidence is superseded",),
                time_horizon="6-12 months",
            )
        else:
            raise AssertionError(self.schema)
        return {"raw": None, "parsed": parsed, "parsing_error": None}


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema, **_kwargs):
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
        assert state["past_context"] == ""
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
    artifact_writer=None,
    memory: MemoryContext | None = None,
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
        memory=memory
        or MemoryContext(
            instrument=request.ticker,
            market="America/New_York",
        ),
        instrument_context="The instrument is NVDA.",
        cancel_requested=lambda: False,
        **({"artifact_writer": artifact_writer} if artifact_writer else {}),
    )


def _memory_context() -> MemoryContext:
    run_id = "prior-run"
    return MemoryContext(
        instrument="NVDA",
        market="America/New_York",
        items=(
            MemoryRecord(
                ref=f"memory:{run_id}",
                run_id=run_id,
                scope="same_ticker",
                ticker="NVDA",
                market="America/New_York",
                analysis_date=date(2026, 6, 30),
                decision=ResearchDecision(
                    rating=ResearchRating.HOLD,
                    confidence=0.55,
                    thesis="Prior demand thesis.",
                    evidence_refs=(),
                    catalysts=(),
                    risks=("Demand slowed.",),
                    invalidation_conditions=("Growth missed.",),
                    time_horizon="6-12 months",
                ),
                outcome=MemoryOutcome(
                    benchmark="SPY",
                    observation_start=date(2026, 7, 1),
                    observation_end=date(2026, 7, 8),
                    holding_intervals=5,
                    raw_return=-0.02,
                    alpha_return=-0.01,
                ),
                reflection="Calibration lesson: demand evidence was overweighted.",
            ),
        ),
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


@pytest.mark.parametrize(
    ("profile", "decision_prompt_count"),
    (
        (RunProfile.FAST, 1),
        (RunProfile.STANDARD, 2),
        (RunProfile.DEEP, 2),
    ),
)
def test_memory_only_enters_profile_decision_nodes_and_refs_are_whitelisted(
    app_settings,
    monkeypatch,
    profile,
    decision_prompt_count,
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
    memory = _memory_context()
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=profile,
        selected_analysts=("market",),
    )

    execution = graph.execute(
        _context(
            app_settings,
            profile,
            analysts=("market",),
            memory=memory,
        ),
        checkpoint_thread_id=f"memory:{profile.value}",
    )

    calls = [*quick.calls, *deep.calls]
    decision_prompts = [
        prompt for schema, prompt in calls if schema == "ResearchDecision"
    ]
    review_prompts = [
        prompt for schema, prompt in calls if schema == "PerspectiveReview"
    ]
    assert len(decision_prompts) == decision_prompt_count
    assert all(
        "Calibration lesson: demand evidence was overweighted." in prompt
        for prompt in decision_prompts
    )
    assert all(
        "Calibration lesson: demand evidence was overweighted." not in prompt
        for prompt in review_prompts
    )
    assert execution.decision.memory_refs == memory.refs
    assert "memory:invented" not in execution.decision.memory_refs
    assert not any(
        ref.startswith("memory:") for ref in execution.decision.evidence_refs
    )


def test_graph_emits_only_typed_visible_research_artifacts(
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
    artifacts: list[ResearchArtifactDraft] = []
    graph = ResearchGraph(
        quick_llm=llm,
        deep_llm=llm,
        profile=RunProfile.STANDARD,
        selected_analysts=("market", "news"),
    )

    graph.execute(
        _context(
            app_settings,
            RunProfile.STANDARD,
            artifact_writer=artifacts.append,
        ),
        checkpoint_thread_id="typed-artifacts",
    )

    assert {(artifact.stage, artifact.role) for artifact in artifacts} == {
        ("analyst", "market"),
        ("analyst", "news"),
        ("perspective", "bull"),
        ("perspective", "bear"),
        ("judge", "research_judge"),
        ("risk", "risk"),
        ("decision", "final_committee"),
    }
    assert all(
        artifact.content_type
        in {
            "analyst_report",
            "perspective_review",
            "research_decision",
        }
        for artifact in artifacts
    )
    assert all("messages" not in artifact.content.model_fields for artifact in artifacts)
    assert {
        artifact.generation_method.value
        for artifact in artifacts
        if artifact.stage != "analyst"
    } == {"tool_call"}


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


def test_analyst_warning_appendix_is_structured_deduplicated_and_removed() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="historical price",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Fixture data.",
        quality=EvidenceQuality.LOW,
    )
    narrative = """Evidence-grounded report.

---

## Data Quality Warnings

- **historical price** (source: fixture): **partial coverage**

## Data Provenance

| Evidence | Source | Requested / cutoff | Effective date / window | Timing status |
|---|---|---|---|---|
| historical price | fixture | 2026-07-24 | 2026-07-24 | partial |
"""

    report = _adapt_analyst_report("market", narrative, [item])

    assert "Data Quality Warnings" not in report.narrative
    assert "Data Provenance" in report.narrative
    assert len(report.warnings) == 1
    assert report.warnings[0].message == (
        "historical price (fixture): partial coverage"
    )
    assert report.warnings[0].evidence_ref == item.ref
