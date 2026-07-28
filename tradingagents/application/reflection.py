"""Outcome reflection for durable research memory."""

from __future__ import annotations

from typing import Any

_LANGUAGE_INSTRUCTIONS = {
    "English (en)": "Write the reflection in English.",
    "Simplified Chinese (简体中文, zh-CN)": (
        "Write the reflection in Simplified Chinese."
    ),
    "Japanese (日本語, ja)": "Write the reflection in Japanese.",
    # Compatibility for persisted pre-zh-CN settings.
    "English": "Write the reflection in English.",
    "Chinese": "Write the reflection in Simplified Chinese.",
    "Japanese": "Write the reflection in Japanese.",
}


class OutcomeReflector:
    """Turn a five-interval observation into bounded research feedback."""

    def __init__(self, llm: Any, *, output_language: str = "English"):
        self.llm = llm
        self.output_language = output_language

    def reflect(
        self,
        *,
        decision: str,
        raw_return: float,
        alpha_return: float,
        benchmark: str,
        ticker: str,
        holding_intervals: int,
        observation_start: str,
        observation_end: str,
    ) -> str:
        language = _LANGUAGE_INSTRUCTIONS.get(
            self.output_language,
            f"Write the reflection in {self.output_language}.",
        )
        system = (
            "You review short-term market feedback on a past research decision. "
            "Write exactly 2-4 plain-prose sentences with no headings or bullets. "
            "First assess directional consistency over the supplied completed-session "
            "window and cite alpha. Then identify only what the observation teaches "
            "about evidence explicitly present in the stored decision. End with one "
            "concrete lesson for a similar analysis. Do not claim this short window "
            "proves or disproves a medium- or long-term thesis. Do not invent causes, "
            "targets, position sizes, entry levels, or account instructions. "
            f"{language}"
        )
        human = (
            f"Instrument: {ticker}\n"
            f"Observation window: {observation_start} to {observation_end} "
            f"({holding_intervals} completed aligned trading intervals)\n"
            f"Raw return: {raw_return:+.1%}\n"
            f"Alpha vs {benchmark}: {alpha_return:+.1%}\n\n"
            f"Stored research decision:\n{decision}"
        )
        response = self.llm.invoke([("system", system), ("human", human)])
        prefix = (
            f"[{observation_start} \u2192 {observation_end} | "
            f"{holding_intervals}d]"
        )
        return f"{prefix}\n{response.content}"
