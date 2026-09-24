from __future__ import annotations

from typing import Any

import pytest

from tests.support.factories import research_decision
from tests.support.synthesis import (
    _core_draft_from_decision,
    _SequenceLLM,
    _state,
    _StaticLLM,
)
from tradingagents.domain.decision import (
    RiskReviewAdjustment,
)
from tradingagents.research.metrics import MetricsCallback
from tradingagents.research.synthesis.decision import invoke_research_decision
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputError,
)


def test_unknown_risk_adjustment_evidence_remains_a_core_failure() -> None:
    state = _state()
    state["risk_reviews"] = {"integrated": {}}
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    invalid = core.model_copy(
        update={
            "risk_review_adjustments": (
                RiskReviewAdjustment(
                    source_role="integrated",
                    disposition="modified",
                    subject="Risk calibration",
                    explanation="The risk review changed confidence.",
                    evidence_refs=("ev_deadbeefdead",),
                ),
            )
        }
    )
    repaired = core.model_copy(
        update={
            "risk_review_adjustments": (
                RiskReviewAdjustment(
                    source_role="integrated",
                    disposition="modified",
                    subject="Risk calibration",
                    explanation="The risk review changed confidence.",
                    evidence_refs=(ref,),
                ),
            )
        }
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionDraft": [
                invalid,
                repaired,
            ],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=True,
    )

    assert result.value.risk_review_adjustments[0].evidence_refs == (ref,)
    assert [schema for schema, _prompt in llm.prompts].count("ResearchDecisionDraft") == 2


@pytest.mark.parametrize(
    ("output_language", "localized_example", "assumption_example"),
    (
        (
            "Simplified Chinese (简体中文, zh-CN)",
            "现有证据支持一项平衡的研究结论",
            "分析师 EPS 共识上修至每股 185–195 日元",
        ),
        (
            "使用正式、克制的繁体中文",
            "The evidence supports a balanced conclusion",
            "Analyst EPS consensus rises to JPY 185-195 per share",
        ),
    ),
)
def test_final_serializers_preserve_output_language_in_primary_and_repair(
    output_language: str,
    localized_example: str,
    assumption_example: str,
) -> None:
    state = _state()
    state["output_language"] = output_language
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    invalid_core = core.model_dump(mode="json")
    invalid_core["thesis"] = ""
    llm = _SequenceLLM(
        {
            "ResearchDecisionDraft": [invalid_core, core],
        }
    )

    invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
        output_language=output_language,
    )

    assert len(llm.prompts) == 2
    assert all(output_language in prompt for _schema, prompt in llm.prompts)
    assert localized_example in llm.prompts[0][1]
    assert localized_example in llm.prompts[1][1]
    assert all(assumption_example in prompt for _schema, prompt in llm.prompts[:2])


def test_final_serializer_phases_record_child_wall_time_without_parent_span() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    metrics = MetricsCallback()
    events: list[dict[str, Any]] = []

    invoke_research_decision(
        _StaticLLM(research_decision(evidence_refs=(ref,))),
        prompt="Form the final decision.",
        state=state,
        node="committee.final.serialize",
        require_risk_adjustments=False,
        event_writer=events.append,
        metrics=metrics,
    )

    snapshot = metrics.snapshot()
    assert "committee.final.serialize" not in snapshot.node_metrics
    assert snapshot.node_metrics["committee.final.serialize.core"].wall_time_seconds >= 0
    assert [
        (event["event_type"], event["node"])
        for event in events
        if event["event_type"].startswith("phase.")
    ] == [
        ("phase.started", "committee.final.serialize.core"),
        ("phase.completed", "committee.final.serialize.core"),
    ]


def test_final_decision_reports_stable_duplicate_scenario_issue() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,))
    duplicate = decision.model_copy(
        update={
            "scenarios": (
                decision.scenarios[0],
                decision.scenarios[0],
                decision.scenarios[2],
            )
        }
    )

    with pytest.raises(StructuredOutputError) as error:
        invoke_research_decision(
            _StaticLLM(duplicate),
            prompt="Form the final decision.",
            state=state,
            node="committee.final",
            require_risk_adjustments=False,
        )

    assert error.value.validation_issues == ("semantic.decision.scenarios.duplicate_kind",)
