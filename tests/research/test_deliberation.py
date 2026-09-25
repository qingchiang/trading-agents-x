from __future__ import annotations

from typing import Any

import pytest

from tests.support.synthesis import (
    _MarkdownLLM,
    _state,
    _state_with_agenda,
    _StaticLLM,
)
from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    DebateImportance,
    ReportLanguage,
)
from tradingagents.domain.reports import (
    DebateAgenda,
    DebateIssue,
    JudgeDraft,
    RebuttalReview,
    RiskReview,
)
from tradingagents.research.synthesis.decision_prompts import (
    decision_reference_label_guidance,
    decision_scenario_assumption_guidance,
)
from tradingagents.research.synthesis.deliberation import (
    debate_round_has_material_progress,
    invoke_debate_agenda,
    invoke_judge_draft,
    invoke_rebuttal,
    invoke_research_case,
    invoke_risk_review,
    write_research_markdown,
)
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputError,
)


def test_research_markdown_uses_inline_ledger_refs_without_definitions() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    result = write_research_markdown(
        _MarkdownLLM(f"# Case\n\nSupported.[^{ref}]\n\n[^{ref}]: Model source text."),
        prompt="Write the case.",
        node="case.bull.write",
        allowed_evidence_refs=(ref,),
        output_language="English (en)",
        allow_continuation=True,
    )

    assert result.markdown == f"# Case\n\nSupported.[^{ref}]"
    assert result.warnings == ()


def test_research_markdown_can_normalize_truncated_output_without_continuation() -> None:
    class TruncatedMarkdownLLM:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, prompt: str, config: Any = None) -> Any:
            del prompt, config
            self.calls += 1
            return type(
                "Message",
                (),
                {
                    "content": "# Incremental update\n\nThe available evidence is limited.",
                    "response_metadata": {"finish_reason": "length"},
                },
            )()

    llm = TruncatedMarkdownLLM()
    result = write_research_markdown(
        llm,
        prompt="Write the update.",
        node="incremental.synthesis.semantic",
        allowed_evidence_refs=(),
        output_language="English (en)",
        allow_continuation=False,
    )

    assert llm.calls == 1
    assert result.markdown == "# Incremental update\n\nThe available evidence is limited."


def test_research_case_preserves_readable_markdown_without_navigation_ids() -> None:
    state = _state()
    llm = _StaticLLM(None)
    markdown = "## Bull case\n\nEvidence supports the constructive view."
    result = invoke_research_case(
        llm,
        role="bull",
        markdown=markdown,
        state=state,
        node="case.bull",
    )

    assert result.value.model_dump() == {"role": "bull", "markdown": markdown}
    assert result.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDITED
    assert llm.prompts == []


def test_research_case_audit_preserves_completed_markdown() -> None:
    state = _state()
    markdown = "## Constructive case\n\n| Measure | Reading |\n|---|---:|\n| Growth | 12.3% |\n"
    result = invoke_research_case(
        _StaticLLM(None),
        role="bull",
        markdown=markdown,
        state=state,
        node="case.bull.audit",
    )

    assert result.value.markdown == markdown


def test_agenda_audit_failure_uses_explicit_navigation_fallback() -> None:
    state = _state()
    llm = _StaticLLM({"summary": "", "issues": []})
    original_context = "FULL AGENDA CONTEXT MUST NOT BE REPEATED"
    result = invoke_debate_agenda(
        llm,
        prompt=original_context,
        state=state,
        node="debate.agenda.audit",
        output_language="English (en)",
    )

    assert result.value.issues[0].id == "debate.issue_audit_fallback"
    assert result.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    assert "INVALID CANDIDATE JSON" in llm.prompts[1]
    assert original_context not in llm.prompts[1]


def test_agenda_prompt_and_fallback_follow_standard_output_language() -> None:
    state = _state()
    language = "Simplified Chinese (简体中文, zh-CN)"
    llm = _StaticLLM({"summary": "", "issues": []})

    result = invoke_debate_agenda(
        llm,
        prompt="已完成的主持人简报。",
        state=state,
        node="debate.agenda.audit",
        output_language=language,
    )

    assert "多空案例对一个重要经营机制存在分歧" in llm.prompts[0]
    assert all(language in prompt for prompt in llm.prompts)
    assert result.value.summary.startswith("已完成的多空案例")
    assert result.value.issues[0].question.endswith("是什么？")


def test_agenda_supports_reasoning_json_mode_transport() -> None:
    state = _state()
    agenda = DebateAgenda(
        summary="One material disagreement requires resolution.",
        issues=(
            DebateIssue(
                id="debate.issue_1",
                question="Will the operating improvement persist?",
                importance=DebateImportance.MATERIAL,
            ),
        ),
    )
    llm = _StaticLLM(agenda)
    llm.preferred_structured_output_method = "json_mode"

    result = invoke_debate_agenda(
        llm,
        prompt="Compare the completed bull and bear cases.",
        state=state,
        node="debate.agenda.serialize",
        output_language="English (en)",
    )

    assert result.value == agenda
    assert len(llm.prompts) == 1
    assert "Return exactly one JSON object" in llm.prompts[0]
    assert '"title": "DebateAgenda"' in llm.prompts[0]


def test_custom_language_agenda_failure_keeps_checkpoint_boundary() -> None:
    state = _state()
    custom_language = "Use formal Chinese and preserve Japanese legal names."

    with pytest.raises(StructuredOutputError):
        invoke_debate_agenda(
            _StaticLLM({"summary": "", "issues": []}),
            prompt="Completed moderator brief.",
            state=state,
            node="debate.agenda.audit",
            output_language=custom_language,
        )


@pytest.mark.parametrize(
    ("conservative_open", "expected_open"),
    [
        (False, ()),
        (True, ("debate.issue_1", "debate.issue_2")),
    ],
)
def test_rebuttal_audit_failure_preserves_markdown_with_profile_fallback(
    conservative_open: bool,
    expected_open: tuple[str, ...],
) -> None:
    state = _state_with_agenda()
    invalid = {
        "addressed_issue_ids": ["debate.issue_invented"],
        "open_issue_ids": ["debate.issue_invented"],
    }
    markdown = "debate.issue_1 is addressed; the other issue is discussed."

    llm = _StaticLLM(invalid)
    result = invoke_rebuttal(
        llm,
        role="bear",
        round_number=1,
        markdown=markdown,
        state=state,
        node="rebuttal.bear.audit",
        conservative_open=conservative_open,
    )

    assert result.value.markdown == markdown
    assert result.value.addressed_issue_ids == ("debate.issue_1",)
    assert result.value.open_issue_ids == expected_open
    assert result.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    assert all("debate.issue_1" in prompt and "debate.issue_2" in prompt for prompt in llm.prompts)


def test_judge_audit_failure_preserves_markdown_without_fabricated_rating() -> None:
    state = _state_with_agenda()
    invalid = {
        "preliminary_rating": "Hold",
        "confidence": 0.5,
        "issue_dispositions": [{"issue_id": "debate.issue_invented", "status": "upheld"}],
    }

    llm = _StaticLLM(invalid)
    result = invoke_judge_draft(
        llm,
        markdown="## Judgment\n\nBoth material questions remain unresolved.",
        state=state,
        node="judge.research.audit",
    )

    assert isinstance(result.value, JudgeDraft)
    assert result.value.preliminary_rating is None
    assert result.value.confidence is None
    assert {item.issue_id: item.status for item in result.value.issue_dispositions} == {
        "debate.issue_1": "unresolved",
        "debate.issue_2": "unresolved",
    }
    assert result.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    assert all("debate.issue_1" in prompt and "debate.issue_2" in prompt for prompt in llm.prompts)


def test_risk_navigation_ignores_unknown_issue_ids_without_llm_audit() -> None:
    state = _state_with_agenda()
    llm = _StaticLLM(
        {
            "challenged_issue_ids": ["debate.issue_invented"],
            "unresolved_issue_ids": ["debate.issue_invented"],
        }
    )
    markdown = (
        "debate.issue_1 is challenged.\nUnresolved: debate.issue_2.\nIgnore debate.issue_invented."
    )

    result = invoke_risk_review(
        llm,
        role="integrated",
        markdown=markdown,
        state=state,
        node="risk.review.audit",
    )

    assert isinstance(result.value, RiskReview)
    assert result.value.challenged_issue_ids == (
        "debate.issue_1",
        "debate.issue_2",
    )
    assert result.value.unresolved_issue_ids == ("debate.issue_2",)
    assert llm.prompts == []


def test_debate_progress_requires_a_changed_open_issue_set() -> None:
    state = _state()
    agenda = DebateAgenda(
        summary="One material issue remains.",
        issues=(
            DebateIssue(
                id="debate.issue_1",
                question="Will the mechanism persist?",
                importance=DebateImportance.MATERIAL,
            ),
            DebateIssue(
                id="debate.issue_2",
                question="Is valuation support durable?",
                importance=DebateImportance.MATERIAL,
            ),
        ),
    )
    state["debate_agenda"] = agenda.model_dump(mode="json")
    first = RebuttalReview(
        role="bull",
        round=1,
        markdown="The operating issue remains open.",
        addressed_issue_ids=("debate.issue_1", "debate.issue_2"),
        open_issue_ids=("debate.issue_1",),
    )
    repeated = first.model_copy(update={"round": 2})
    changed = repeated.model_copy(update={"open_issue_ids": ("debate.issue_2",)})

    state["rebuttals"] = [first.model_dump(mode="json")]
    assert debate_round_has_material_progress(state, round_number=1) is True

    state["rebuttals"].append(repeated.model_dump(mode="json"))
    assert debate_round_has_material_progress(state, round_number=2) is False

    state["rebuttals"][-1] = changed.model_dump(mode="json")
    assert debate_round_has_material_progress(state, round_number=2) is True


@pytest.mark.parametrize(
    ("output_language", "expected_label"),
    (
        (ReportLanguage.ENGLISH.prompt_label, "analyst target lower bound"),
        (ReportLanguage.SIMPLIFIED_CHINESE.prompt_label, "目标价下限"),
        (ReportLanguage.JAPANESE.prompt_label, "目標株価下限"),
    ),
)
def test_singleton_target_label_guidance_is_localized(
    output_language: str,
    expected_label: str,
) -> None:
    guidance = decision_reference_label_guidance(output_language)

    assert expected_label in guidance
    assert "two distinct" in guidance
    assert "Never duplicate one value ref" in guidance


def test_6501_scenario_assumption_regression_is_covered_by_prompt_guidance() -> None:
    guidance = decision_scenario_assumption_guidance(ReportLanguage.SIMPLIFIED_CHINESE.prompt_label)

    assert "分析师 EPS 共识上修至每股 185–195 日元" in guidance
    assert "EPS" in guidance
    assert "不要只写‘共识上修至 185–195 日元’" in guidance
