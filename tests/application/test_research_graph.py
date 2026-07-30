from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

import tradingagents.graph.research_graph as research_graph_module
from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalystClaim,
    AnalystClaimType,
    AnalystReport,
    AnalystSection,
    DebateAgenda,
    DebateImportance,
    DebateResolution,
    DisputeRuling,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    JudgeDraft,
    MemoryContext,
    MemoryOutcome,
    MemoryRecord,
    RebuttalOutcome,
    RebuttalPoint,
    RebuttalReview,
    ResearchArtifactDraft,
    ResearchCase,
    ResearchCaseArgument,
    ResearchDecision,
    ResearchRating,
    ResearchWarning,
    RiskFinding,
    RiskFindingKind,
    RiskReview,
    RiskReviewAdjustment,
    RiskReviewDisposition,
    RiskSeverity,
    RunProfile,
)
from tradingagents.application.metrics import MetricsCallback
from tradingagents.application.runtime import RunContext
from tradingagents.graph.research_graph import (
    ResearchGraph,
    _evidence_from_record,
    _evidence_warnings,
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
        if self.schema is AnalystReport:
            analyst = re.search(
                r"You are the (market|social|news|fundamentals) analyst",
                prompt,
            ).group(1)
            section_ids = {
                "market": (
                    "trend",
                    "market_regime",
                    "price_volume",
                    "momentum",
                    "volatility",
                    "counter_evidence",
                    "market_reference_levels",
                ),
                "social": (
                    "overall_sentiment",
                    "source_assessments",
                    "consensus_divergence",
                    "dominant_themes",
                    "catalysts_risks",
                    "coverage_limits",
                ),
                "news": (
                    "company_events",
                    "disclosures",
                    "industry_macro",
                    "event_timeline",
                    "impact_paths",
                    "relevance",
                ),
                "fundamentals": (
                    "business",
                    "growth",
                    "profitability_quality",
                    "cash_flow",
                    "balance_sheet",
                    "valuation",
                    "disclosure_limits",
                ),
            }[analyst]
            parsed = AnalystReport(
                analyst=analyst,
                executive_summary="Complete fixture analyst summary.",
                confidence=0.6,
                claims=(
                    AnalystClaim(
                        id=f"{analyst}.claim_1",
                        kind=AnalystClaimType.INFERENCE,
                        statement="Fixture evidence is mixed.",
                        implication="The conclusion should preserve uncertainty.",
                        confidence=0.6,
                        evidence_refs=refs[-1:],
                    ),
                ),
                sections=tuple(
                    AnalystSection(
                        id=section_id,
                        title=section_id.replace("_", " ").title(),
                        narrative=(f"Detailed fixture analysis grounded in {refs[-1]}."),
                    )
                    for section_id in section_ids
                ),
                risks=("Evidence quality may deteriorate.",),
                invalidation_conditions=("New evidence contradicts the fixture.",),
                evidence_refs=refs[-1:],
            )
        elif self.schema is ResearchCase:
            role = "bull" if "Bull Researcher" in prompt else "bear"
            parsed = ResearchCase(
                role=role,
                executive_summary=f"Complete {role} case.",
                thesis=f"The {role} case remains conditional.",
                arguments=(
                    ResearchCaseArgument(
                        id=f"case.{role}.argument_1",
                        claim_ids=("market.claim_1",),
                        statement="A fixture claim supports the case.",
                        mechanism="The cited mechanism affects the outcome.",
                        implication="The committee should test this condition.",
                        confidence=0.6,
                        evidence_refs=refs[-1:],
                    ),
                ),
                strongest_counterarguments=("The opposing interpretation remains plausible.",),
                fragile_assumptions=("The mechanism persists.",),
                risks=("Evidence quality may deteriorate.",),
                evidence_refs=refs[-1:],
            )
        elif self.schema is DebateAgenda:
            parsed = DebateAgenda(
                executive_summary="One material dispute requires resolution.",
                issues=(
                    {
                        "id": "debate.issue_1",
                        "question": "Will the fixture mechanism persist?",
                        "claim_ids": ("market.claim_1",),
                        "importance": DebateImportance.MATERIAL,
                        "bull_position": "The mechanism persists.",
                        "bear_position": "The mechanism fades.",
                        "evidence_refs": refs[-1:],
                    },
                ),
                evidence_refs=refs[-1:],
            )
        elif self.schema is RebuttalReview:
            role = "bull" if "Bull Researcher Rebuttal" in prompt else "bear"
            round_number = int(re.search(r"CURRENT ROUND: (\d+)", prompt).group(1))
            parsed = RebuttalReview(
                role=role,
                round=round_number,
                thesis_update="The case remains conditional.",
                responses=(
                    RebuttalPoint(
                        agenda_id="debate.issue_1",
                        claim_ids=("market.claim_1",),
                        response="The opposing interpretation is incomplete.",
                        causal_mechanism=("The fixture mechanism has a role-specific path."),
                        outcome=RebuttalOutcome.UNRESOLVED,
                        evidence_refs=refs[-1:],
                        remaining_questions=("Which mechanism dominates?",),
                    ),
                ),
                evidence_refs=refs[-1:],
                remaining_questions=("Which mechanism dominates?",),
            )
        elif self.schema is JudgeDraft:
            parsed = JudgeDraft(
                preliminary_rating=ResearchRating.HOLD,
                confidence=0.6,
                executive_summary="The debate supports a balanced draft.",
                thesis="The fixture conclusion remains conditional.",
                rulings=(
                    DisputeRuling(
                        agenda_id="debate.issue_1",
                        resolution=DebateResolution.MIXED,
                        rationale="Both cases retain evidentiary support.",
                        accepted_claim_ids=("market.claim_1",),
                        evidence_refs=refs[-1:],
                    ),
                ),
                risks=("Evidence quality may deteriorate.",),
                invalidation_conditions=("New evidence contradicts the fixture.",),
                unresolved_questions=("Which mechanism dominates?",),
                time_horizon="6-12 months",
                evidence_refs=refs[-1:],
                memory_refs=memory_refs[:1],
            )
        elif self.schema is RiskReview:
            role = next(
                (
                    candidate
                    for candidate in (
                        "integrated",
                        "aggressive",
                        "neutral",
                        "conservative",
                    )
                    if (
                        candidate.title() in prompt
                        or (candidate == "integrated" and "Integrated Risk Reviewer" in prompt)
                    )
                ),
                "integrated",
            )
            parsed = RiskReview(
                role=role,
                executive_summary="The draft needs a qualification.",
                findings=(
                    RiskFinding(
                        id=f"risk.{role}.finding_1",
                        kind=RiskFindingKind.BASE_CONSISTENCY,
                        statement="Confidence exceeds fixture certainty.",
                        mechanism="Uncertainty widens the result range.",
                        severity=RiskSeverity.MEDIUM,
                        related_claim_ids=("market.claim_1",),
                        evidence_refs=refs[-1:],
                    ),
                ),
                invalidation_paths=("The accepted mechanism fails.",),
                recommended_changes=("Preserve uncertainty.",),
                confidence_adjustment=-0.05,
                evidence_refs=refs[-1:],
            )
        elif self.schema is ResearchDecision:
            risk_roles = tuple(
                role
                for role in (
                    "integrated",
                    "aggressive",
                    "neutral",
                    "conservative",
                )
                if f'"role": "{role}"' in prompt
            )
            adjustments = (
                tuple(
                    RiskReviewAdjustment(
                        source_role=role,
                        disposition=RiskReviewDisposition.MODIFIED,
                        subject=f"{role} review",
                        explanation="The committee calibrated the fixture.",
                        evidence_refs=refs[-1:],
                    )
                    for role in risk_roles
                )
                if "RISK REVIEWS:" in prompt
                else ()
            )
            parsed = research_decision(
                confidence=0.65,
                thesis="The available evidence supports a balanced conclusion.",
                evidence_refs=refs[-1:],
                memory_refs=memory_refs[:1],
                catalysts=("Evidence improves",),
                risks=("Evidence deteriorates",),
                invalidation_conditions=("The cited evidence is superseded",),
                risk_review_adjustments=adjustments,
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
        assert _kwargs["config"]["metadata"] == {"research_node": f"analyst.{self.analyst}.collect"}
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
                decision=research_decision(
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
            {"case.bull", "judge.research", "risk.review"},
        ),
        (
            RunProfile.STANDARD,
            {
                "case.bull",
                "case.bear",
                "debate.agenda",
                "rebuttal.bull",
                "rebuttal.bear",
                "judge.research",
                "risk.review",
                "committee.final",
            },
            {"risk.aggressive", "risk.conservative"},
        ),
        (
            RunProfile.DEEP,
            {
                "rebuttal.bull",
                "rebuttal.bear",
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
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
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

    completed = {event["node"] for event in events if event["event_type"] == "node.completed"}
    assert required_nodes <= completed
    assert not forbidden_nodes & completed
    assert set(execution.reports) == {"market", "news"}
    assert execution.evidence.version == "3"
    assert execution.evidence.digest
    assert execution.decision.rating is ResearchRating.HOLD
    valid_refs = {item.ref for item in execution.evidence.items}
    assert set(execution.decision.evidence_refs) <= valid_refs


@pytest.mark.parametrize(
    ("profile", "quick_schemas", "deep_schemas"),
    (
        (
            RunProfile.FAST,
            {"AnalystReport"},
            {"ResearchDecision"},
        ),
        (
            RunProfile.STANDARD,
            {
                "AnalystReport",
                "ResearchCase",
                "DebateAgenda",
                "RebuttalReview",
                "RiskReview",
            },
            {"JudgeDraft", "ResearchDecision"},
        ),
        (
            RunProfile.DEEP,
            {"AnalystReport"},
            {
                "ResearchCase",
                "DebateAgenda",
                "RebuttalReview",
                "JudgeDraft",
                "RiskReview",
                "ResearchDecision",
            },
        ),
    ),
)
def test_profiles_route_quality_roles_to_the_configured_model_tier(
    app_settings,
    monkeypatch,
    profile: RunProfile,
    quick_schemas: set[str],
    deep_schemas: set[str],
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
    )
    quick = _FakeLLM()
    deep = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        profile=profile,
        selected_analysts=("market",),
    )

    graph.execute(
        _context(app_settings, profile, analysts=("market",)),
        checkpoint_thread_id=f"model-routing:{profile.value}",
    )

    assert {schema for schema, _prompt in quick.calls} == quick_schemas
    assert {schema for schema, _prompt in deep.calls} == deep_schemas


def test_frozen_execution_uses_production_deliberation_without_analyst_calls(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {},
    )
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="frozen tool response",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Frozen operating evidence is mixed.",
    )
    bundle = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    report = analyst_report(
        analyst="market",
        evidence_ref=item.ref,
        narrative="Frozen evidence-grounded market analysis.",
    )
    llm = _FakeLLM()
    artifacts: list[ResearchArtifactDraft] = []
    graph = ResearchGraph(
        quick_llm=llm,
        deep_llm=llm,
        profile=RunProfile.STANDARD,
        selected_analysts=("market",),
    )
    context = _context(
        app_settings,
        RunProfile.STANDARD,
        analysts=("market",),
        artifact_writer=artifacts.append,
    )

    execution = graph.execute_frozen(
        context,
        evidence=bundle,
        reports={"market": report},
    )

    assert all(schema != "AnalystReport" for schema, _prompt in llm.calls)
    assert all(artifact.stage != "analyst" for artifact in artifacts)
    assert execution.evidence == bundle
    assert execution.reports == {"market": report}
    assert execution.decision.rating is ResearchRating.HOLD


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
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
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
        prompt for schema, prompt in calls if schema in {"ResearchDecision", "JudgeDraft"}
    ]
    nondecision_prompts = [
        prompt
        for schema, prompt in calls
        if schema not in {"ResearchDecision", "JudgeDraft", "AnalystReport"}
    ]
    assert len(decision_prompts) == decision_prompt_count
    assert all(
        "Calibration lesson: demand evidence was overweighted." in prompt
        for prompt in decision_prompts
    )
    assert all(
        "Calibration lesson: demand evidence was overweighted." not in prompt
        for prompt in nondecision_prompts
    )
    assert execution.decision.memory_refs == memory.refs
    assert "memory:invented" not in execution.decision.memory_refs
    assert not any(ref.startswith("memory:") for ref in execution.decision.evidence_refs)


def test_graph_emits_only_typed_visible_research_artifacts(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
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
        ("case", "bull"),
        ("case", "bear"),
        ("agenda", "moderator"),
        ("rebuttal", "bull"),
        ("rebuttal", "bear"),
        ("judge", "research_judge"),
        ("risk", "integrated"),
        ("decision", "final_committee"),
    }
    assert all(
        artifact.content_type
        in {
            "analyst_report",
            "research_case",
            "debate_agenda",
            "rebuttal_review",
            "judge_draft",
            "risk_review",
            "research_decision",
        }
        for artifact in artifacts
    )
    assert all("messages" not in artifact.content.model_fields for artifact in artifacts)
    assert {
        artifact.generation_method.value for artifact in artifacts if artifact.stage != "analyst"
    } == {"tool_call"}
    assert {
        (artifact.role, artifact.prompt_version)
        for artifact in artifacts
        if artifact.stage == "analyst"
    } == {
        ("market", "analyst-market-v3-workset"),
        ("news", "analyst-news-v3-workset"),
    }
    assert all(artifact.prompt_version != "research-v1" for artifact in artifacts)


def test_deep_debate_stops_when_rebuttals_add_no_new_information(
    app_settings,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda self: {analyst: _AnalystSubgraph(analyst) for analyst in self.selected_analysts},
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

    assert execution.state["rebuttal_round"] <= 3


def test_retry_reuses_checkpointed_analyst_tool_artifacts(
    app_settings,
    monkeypatch,
) -> None:
    subgraph = _AnalystSubgraph("market")
    collection_calls = 0
    original_invoke = subgraph.invoke

    def counted_invoke(state, **kwargs):
        nonlocal collection_calls
        collection_calls += 1
        return original_invoke(state, **kwargs)

    subgraph.invoke = counted_invoke
    monkeypatch.setattr(
        ResearchGraph,
        "_build_analyst_subgraphs",
        lambda _self: {"market": subgraph},
    )
    original_synthesis = research_graph_module._invoke_analyst_report
    synthesis_calls = 0

    def fail_once(*args, **kwargs):
        nonlocal synthesis_calls
        synthesis_calls += 1
        if synthesis_calls == 1:
            raise RuntimeError("fixture synthesis failure")
        return original_synthesis(*args, **kwargs)

    monkeypatch.setattr(
        research_graph_module,
        "_invoke_analyst_report",
        fail_once,
    )
    graph = ResearchGraph(
        quick_llm=_FakeLLM(),
        deep_llm=_FakeLLM(),
        profile=RunProfile.FAST,
        selected_analysts=("market",),
    )
    context = _context(
        app_settings,
        RunProfile.FAST,
        analysts=("market",),
    )
    checkpointer = MemorySaver()

    with pytest.raises(RuntimeError, match="fixture synthesis failure"):
        graph.execute(
            context,
            checkpointer=checkpointer,
            checkpoint_thread_id="analyst-artifact-retry",
        )

    execution = graph.execute(
        context,
        checkpointer=checkpointer,
        checkpoint_thread_id="analyst-artifact-retry",
        resume=True,
    )

    assert execution.decision.rating is ResearchRating.HOLD
    assert collection_calls == 1
    assert synthesis_calls == 2


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


def test_analyst_warning_is_derived_from_evidence_quality() -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="historical price",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Fixture data.",
        quality=EvidenceQuality.LOW,
    )
    warnings = _evidence_warnings([item])

    assert len(warnings) == 1
    assert warnings[0].message == ("historical price from fixture has low evidence quality.")
    assert warnings[0].evidence_ref == item.ref


def test_sentiment_confidence_and_fallback_warning_reach_typed_handoff() -> None:
    warning = ResearchWarning(
        code="agent.structured_output_fallback",
        message="Sentiment output used the free-text fallback.",
        source="Sentiment Analyst",
    )

    report = analyst_report(
        analyst="social",
        confidence=0.55,
        warnings=(warning,),
        narrative="Preserved structured sentiment report.",
    )

    assert report.confidence == 0.55
    assert report.warnings == (warning,)
