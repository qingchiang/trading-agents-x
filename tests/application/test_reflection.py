from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradingagents.application.reflection import OutcomeReflector


class _RecordingLLM:
    def __init__(self, content: str = "Bounded fixture reflection."):
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
def test_reflection_prefix_is_deterministic_and_language_neutral(
    start,
    end,
    intervals,
) -> None:
    reflector = OutcomeReflector(
        _RecordingLLM("任意の言語の本文。"),
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

    assert result == f"[{start} → {end} | {intervals}d]\n任意の言語の本文。"


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
    assert "short window" in system
    assert "Do not invent causes" in system
    assert "position sizes" in system
    assert "account instructions" in system
    assert "Method lesson:" in system
    assert "Alpha vs SPY: +1.2%" in human
    assert "5 completed aligned trading intervals" in human
    assert "Demand evidence improved" in human
