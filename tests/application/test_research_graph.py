from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

import tradingagents.graph.research_graph as research_graph_module
from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalystClaimType,
    ClaimImportance,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    IssueDisposition,
    KeyClaim,
    MemoryContext,
    MemoryOutcome,
    MemoryRecord,
    ResearchArtifactDraft,
    ResearchRating,
    ResearchWarning,
    RiskReviewAdjustment,
    RiskReviewDisposition,
    RunProfile,
)
from tradingagents.application.metrics import MetricsCallback
from tradingagents.application.runtime import RunContext
from tradingagents.graph.analyst_synthesis import AnalystAuditDraft
from tradingagents.graph.deliberation import (
    DecisionNumericDraft,
    JudgeAudit,
    RebuttalAudit,
    ResearchDecisionCoreDraft,
)
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

    def invoke(self, prompt, config=None):
        assert config is None or "metadata" in config
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
        if self.schema is AnalystAuditDraft:
            analyst = re.search(
                r"existing\n(market|social|news|fundamentals) report",
                prompt,
            ).group(1)
            section_id = f"{analyst}.section_1"
            parsed = AnalystAuditDraft(
                confidence=0.6,
                key_claims=(
                    KeyClaim(
                        id=f"{analyst}.claim_1",
                        section_id=section_id,
                        kind=AnalystClaimType.INFERENCE,
                        importance=ClaimImportance.PRIMARY,
                        statement="Fixture evidence is mixed.",
                        implication="The conclusion should preserve uncertainty.",
                        confidence=0.6,
                        evidence_refs=refs[-1:],
                    ),
                ),
                section_source_refs={section_id: refs[-1:]},
            )
        elif self.schema is DebateAgenda:
            parsed = DebateAgenda(
                summary="One material dispute requires resolution.",
                issues=(
                    DebateIssue(
                        id="debate.issue_1",
                        question="Will the fixture mechanism persist?",
                        importance=DebateImportance.MATERIAL,
                    ),
                ),
            )
        elif self.schema is RebuttalAudit:
            parsed = RebuttalAudit(
                addressed_issue_ids=("debate.issue_1",),
                open_issue_ids=("debate.issue_1",),
            )
        elif self.schema is JudgeAudit:
            parsed = JudgeAudit(
                preliminary_rating=ResearchRating.HOLD,
                confidence=0.6,
                issue_dispositions=(
                    IssueDisposition(
                        issue_id="debate.issue_1",
                        status="unresolved",
                    ),
                ),
            )
        elif self.schema is ResearchDecisionCoreDraft:
            risk_roles = tuple(
                role
                for role in (
                    "integrated",
                    "aggressive",
                    "neutral",
                    "conservative",
                )
                if f'"{role}"' in prompt
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
                if "REQUIRED RISK REVIEW ROLES:" in prompt
                else ()
            )
            decision = research_decision(
                confidence=0.65,
                thesis="The available evidence supports a balanced conclusion.",
                evidence_refs=refs[-1:],
                memory_refs=memory_refs[:1],
                catalysts=("Evidence improves",),
                risks=("Evidence deteriorates",),
                invalidation_conditions=("The cited evidence is superseded",),
                risk_review_adjustments=adjustments,
            )
            payload = decision.model_dump(mode="json")
            payload.pop("valuation_assessment", None)
            payload.pop("market_reference_levels", None)
            payload.pop("calculation_records", None)
            payload.pop("numeric_audit_status", None)
            for scenario in payload["scenarios"]:
                scenario.pop("reference_range", None)
            parsed = ResearchDecisionCoreDraft.model_validate(payload)
        elif self.schema is DecisionNumericDraft:
            parsed = DecisionNumericDraft(requested=False)
        else:
            raise AssertionError(self.schema)
        return {"raw": None, "parsed": parsed, "parsing_error": None}


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema, **_kwargs):
        return _StructuredInvoker(schema, self.calls)

    def invoke(self, prompt, config=None):
        assert config is None or "metadata" in config
        assert not isinstance(prompt, list), (
            "research graph must not invoke an LLM evidence-preparation pass"
        )
        call_type = (
            "MarkdownReport"
            if re.search(
                r"You are the (market|social|news|fundamentals) analyst",
                prompt,
            )
            else "ResearchMarkdown"
        )
        self.calls.append((call_type, prompt))
        refs = tuple(dict.fromkeys(re.findall(r"ev_[a-f0-9]{12}", prompt)))
        analyst_match = re.search(
            r"You are the (market|social|news|fundamentals) analyst",
            prompt,
        )
        analyst = analyst_match.group(1) if analyst_match else "market"
        citation = f"[^{refs[-1]}]" if refs else ""
        return AIMessage(
            content=(
                f"# {analyst.title()} analysis\n\n"
                f"Fixture evidence is mixed. {citation}"
            )
        )


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
    assert execution.evidence.version == "5"
    assert execution.evidence.digest
    assert execution.decision.rating is ResearchRating.HOLD
    valid_refs = {item.ref for item in execution.evidence.items}
    assert set(execution.decision.evidence_refs) <= valid_refs
    event_positions = {
        (event["event_type"], event["node"]): index
        for index, event in enumerate(events)
        if event["event_type"] in {"node.started", "node.completed"}
    }
    assert event_positions[("node.completed", "evidence.seal")] < min(
        event_positions[("node.started", f"analyst.{analyst}")]
        for analyst in ("market", "news")
    )
    assert max(
        event_positions[("node.completed", f"analyst.{analyst}")]
        for analyst in ("market", "news")
    ) < event_positions[("node.completed", "reports.ready")]
    assert not any(
        node.endswith(".prepare")
        for node in graph.metrics.snapshot().node_metrics
    )
    if profile is RunProfile.STANDARD:
        node_metrics = graph.metrics.snapshot().node_metrics
        assert {
                "case.bull.write",
                "case.bull.audit",
                "committee.final.reason",
                "committee.final.serialize.core",
                "committee.final.serialize.numeric",
            } <= set(node_metrics)
        assert not any(node.endswith(".prepare") for node in node_metrics)
        assert "case.bull" not in node_metrics
        final_prompt = next(
            prompt
            for schema, prompt in deep.calls
            if schema == "ResearchDecisionCoreDraft"
        )
        assert "DECISION SYNTHESIS BRIEF:" in final_prompt
        assert "RESEARCH CONTEXT:" not in final_prompt
        assert "REQUIRED RISK REVIEW ROLES:" in final_prompt
        agenda_prompt = next(
            prompt for schema, prompt in quick.calls if schema == "DebateAgenda"
        )
        assert '"evidence_catalog"' not in agenda_prompt
        assert '"analyst_reports"' not in agenda_prompt
        agenda_context_event = next(
            event
            for event in events
            if event["event_type"] == "node.context_prepared"
            and event["node"] == "debate.agenda.context"
        )
        assert agenda_context_event["payload"]["catalog_items"] == 0
        assert agenda_context_event["payload"]["catalog_tables"] == 0


@pytest.mark.parametrize(
    ("profile", "quick_schemas", "deep_schemas"),
    (
        (
            RunProfile.FAST,
            {
                "MarkdownReport",
                "AnalystAuditDraft",
            },
            {
                "ResearchDecisionCoreDraft",
                "DecisionNumericDraft",
                "ResearchMarkdown",
            },
        ),
        (
            RunProfile.STANDARD,
            {
                "MarkdownReport",
                "AnalystAuditDraft",
                "DebateAgenda",
                "RebuttalAudit",
                "ResearchMarkdown",
            },
            {
                "JudgeAudit",
                "ResearchDecisionCoreDraft",
                "DecisionNumericDraft",
                "ResearchMarkdown",
            },
        ),
        (
            RunProfile.DEEP,
            {
                "MarkdownReport",
                "AnalystAuditDraft",
            },
            {
                "DebateAgenda",
                "RebuttalAudit",
                "JudgeAudit",
                "ResearchDecisionCoreDraft",
                "DecisionNumericDraft",
                "ResearchMarkdown",
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


@pytest.mark.parametrize(
    ("profile", "role", "expected_tier"),
    (
        (RunProfile.FAST, "bull", "quick"),
        (RunProfile.STANDARD, "bull", "quick"),
        (RunProfile.STANDARD, "risk", "quick"),
        (RunProfile.DEEP, "bull", "deep"),
        (RunProfile.DEEP, "risk", "deep"),
    ),
)
def test_profile_keeps_reasoning_and_serializer_on_the_same_model_tier(
    profile: RunProfile,
    role: str,
    expected_tier: str,
) -> None:
    quick = _FakeLLM()
    deep = _FakeLLM()
    quick_serializer = _FakeLLM()
    deep_serializer = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=quick,
        deep_llm=deep,
        quick_serializer_llm=quick_serializer,
        deep_serializer_llm=deep_serializer,
        profile=profile,
        selected_analysts=("market",),
    )
    spec = research_graph_module._PERSPECTIVE_SPECS[role]

    if expected_tier == "deep":
        assert graph._deliberation_llm(spec) is deep
        assert graph._deliberation_serializer_llm(spec) is deep_serializer
    else:
        assert graph._deliberation_llm(spec) is quick
        assert graph._deliberation_serializer_llm(spec) is quick_serializer


def test_analyst_core_uses_the_dedicated_serializer_client(
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
    reasoning = _FakeLLM()
    serializer = _FakeLLM()
    deep = _FakeLLM()
    graph = ResearchGraph(
        quick_llm=reasoning,
        deep_llm=deep,
        quick_serializer_llm=serializer,
        profile=RunProfile.FAST,
        selected_analysts=("market",),
    )

    graph.execute(
        _context(
            app_settings,
            RunProfile.FAST,
            analysts=("market",),
        ),
        checkpoint_thread_id="dedicated-serializer",
    )

    assert all(
        schema != "AnalystAuditDraft"
        for schema, _prompt in reasoning.calls
    )
    assert {
        schema for schema, _prompt in serializer.calls
    } == {"AnalystAuditDraft"}
    assert {schema for schema, _prompt in reasoning.calls} == {
        "MarkdownReport",
    }


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

    assert all(
        schema not in {"AnalystAuditDraft", "MarkdownReport"}
        for schema, _prompt in llm.calls
    )
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
        prompt
        for schema, prompt in calls
        if schema == "ResearchMarkdown"
        and (
            "Research Judge" in str(prompt)
            or "Final Research Committee" in str(prompt)
        )
    ]
    memory_prompts = [
        str(prompt)
        for _schema, prompt in calls
        if "Calibration lesson: demand evidence was overweighted."
        in str(prompt)
    ]
    assert len(decision_prompts) == decision_prompt_count
    assert all(
        "Calibration lesson: demand evidence was overweighted." in prompt
        for prompt in decision_prompts
    )
    assert all(
        (
            "Research Judge" in prompt
            or "Final Research Committee" in prompt
            or "DECISION SYNTHESIS BRIEF" in prompt
        )
        for prompt in memory_prompts
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
    } == {"tool_call", "markdown_audited"}
    assert {
        (artifact.role, artifact.prompt_version)
        for artifact in artifacts
        if artifact.stage == "analyst"
    } == {
        ("market", "analyst-market-v6-sealed-context"),
        ("news", "analyst-news-v6-sealed-context"),
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
