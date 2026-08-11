from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradingagents.application.reflection import (
    OutcomeReflector,
    ReflectionDraftValidationError,
)


class _RecordingLLM:
    def __init__(
        self,
        content: str = (
            '{"directional_assessment":"mixed",'
            '"source_decision_evidence_lesson":"Compare the stored evidence.",'
            '"method_lesson":"Use a bounded methodological review."}'
        ),
    ):
        self.content = content
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.content)


@pytest.mark.parametrize(
    ("language", "instruction"),
    (
        ("English", "in English"),
        ("Chinese", "in Simplified Chinese"),
        ("Japanese", "in Japanese"),
        ("French", "in French"),
    ),
)
def test_reflector_applies_output_language(language, instruction) -> None:
    llm = _RecordingLLM()
    reflector = OutcomeReflector(llm, output_language=language)

    reflector.reflect(
        decision='{"rating":"Hold"}',
        raw_return=0.02,
        alpha_return=-0.01,
        benchmark="SPY",
        ticker="NVDA",
        holding_intervals=5,
        observation_start="2026-07-20",
        observation_end="2026-07-27",
    )

    system = llm.calls[0][0][1]
    assert instruction in system


@pytest.mark.parametrize(
    ("start", "end", "intervals"),
    (
        ("2026-01-05", "2026-01-12", 5),
        ("2026-02-02", "2026-02-09", 5),
        ("2026-03-03", "2026-03-10", 5),
        ("2026-04-06", "2026-04-13", 5),
        ("2026-05-11", "2026-05-18", 5),
    ),
)
def test_reflection_rendering_is_deterministic_and_language_neutral(
    start,
    end,
    intervals,
) -> None:
    reflector = OutcomeReflector(
        _RecordingLLM(
            '{"directional_assessment":"mixed",'
            '"source_decision_evidence_lesson":"任意の言語の根拠。",'
            '"method_lesson":"任意の言語の方法。"}'
        ),
        output_language="Japanese",
    )

    result = reflector.reflect(
        decision="Fixture decision",
        raw_return=0.01,
        alpha_return=0.02,
        benchmark="SPY",
        ticker="NVDA",
        holding_intervals=intervals,
        observation_start=start,
        observation_end=end,
    )

    assert result.readable_text == (
        "Directional assessment: mixed\n"
        "Source-decision evidence lesson: 任意の言語の根拠。\n"
        "Method lesson\n任意の言語の方法。"
    )


def test_reflection_prompt_is_short_term_research_feedback_only() -> None:
    llm = _RecordingLLM()
    reflector = OutcomeReflector(llm)

    reflector.reflect(
        decision="Demand evidence improved, but valuation remains uncertain.",
        raw_return=0.034,
        alpha_return=0.012,
        benchmark="SPY",
        ticker="NVDA",
        holding_intervals=5,
        observation_start="2026-07-20",
        observation_end="2026-07-27",
    )

    system = llm.calls[0][0][1]
    human = llm.calls[0][1][1]
    assert "short-horizon" in system
    assert "Do not invent causes" in system
    assert "position sizes" in system
    assert "account instructions" in system
    assert "method_lesson" in system
    assert "do not prove or disprove" not in system
    assert "Alpha vs SPY: +1.2%" in human
    assert "5 completed aligned trading intervals" in human
    assert "Demand evidence improved" in human


def test_reflector_returns_a_versioned_structured_draft() -> None:
    reflector = OutcomeReflector(
        _RecordingLLM(
            '{"directional_assessment":"mixed",'
            '"source_decision_evidence_lesson":"Compare the decision evidence '
            'with the observed alpha.",'
            '"method_lesson":"Use a bounded methodological review."}'
        )
    )

    draft = reflector.reflect(
        decision="Fixture decision",
        raw_return=0.01,
        alpha_return=-0.02,
        benchmark="SPY",
        ticker="NVDA",
        holding_intervals=5,
        observation_start="2026-07-20",
        observation_end="2026-07-27",
    )

    assert draft.schema_version == "outcome_reflection.v1"
    assert draft.directional_assessment == "mixed"
    assert draft.method_lesson == "Use a bounded methodological review."
    assert "Method lesson:" not in draft.readable_text


def test_reflector_exposes_typed_invalid_candidate_for_one_bounded_repair() -> None:
    reflector = OutcomeReflector(
        _RecordingLLM('{"method_lesson":"missing fields","api_key":"secret"}')
    )

    with pytest.raises(ReflectionDraftValidationError) as error:
        reflector.reflect(
            decision="Fixture decision",
            raw_return=0.01,
            alpha_return=-0.02,
            benchmark="SPY",
            ticker="NVDA",
            holding_intervals=5,
            observation_start="2026-07-20",
            observation_end="2026-07-27",
        )

    assert error.value.validation_issues
    assert error.value.candidate == '{"method_lesson":"missing fields","api_key":"[REDACTED]"}'


def test_reflector_keeps_provider_usage_separate_from_the_draft() -> None:
    class _UsageLLM(_RecordingLLM):
        def invoke(self, messages):
            response = super().invoke(messages)
            response.usage_metadata = {"input_tokens": 11, "output_tokens": 7}
            return response

    draft = OutcomeReflector(_UsageLLM()).reflect(
        decision="Fixture decision",
        raw_return=0.01,
        alpha_return=-0.02,
        benchmark="SPY",
        ticker="NVDA",
        holding_intervals=5,
        observation_start="2026-07-20",
        observation_end="2026-07-27",
    )

    assert draft.usage["input_tokens"] == 11
    assert draft.usage["output_tokens"] == 7
    assert "usage" not in draft.audit_candidate()
