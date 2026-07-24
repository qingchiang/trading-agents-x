# tradingagents/graph/reflection.py

from typing import Any

from tradingagents.agents.utils.agent_utils import get_language_instruction


class Reflector:
    """Handles reflection on trading decisions."""

    def __init__(self, quick_thinking_llm: Any):
        """Initialize the reflector with an LLM."""
        self.quick_thinking_llm = quick_thinking_llm
        self.log_reflection_prompt = self._get_log_reflection_prompt()

    def _get_log_reflection_prompt(self) -> str:
        """Concise prompt for reflect_on_final_decision (Phase B log entries).

        Produces 2-4 sentences of plain prose — compact enough to be re-injected
        into future agent prompts without bloating the context window.
        """
        return (
            "You are a trading analyst reviewing the short-term market feedback "
            "on your own past decision.\n"
            "Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).\n\n"
            "Cover in order:\n"
            "1. Assess the directional call over the supplied completed-session "
            "window and cite the alpha figure.\n"
            "2. State only whether the short-term price action is consistent or "
            "inconsistent with thesis evidence explicitly present in the decision.\n"
            "3. One concrete lesson to apply to the next similar analysis.\n\n"
            "Do not claim that this short window proves or disproves a medium- or "
            "long-term thesis. Do not invent a target price, time horizon, thesis "
            "claim, or causal explanation that is absent from the decision.\n"
            "Be specific and terse. Your output will be stored verbatim in a decision log "
            "and re-read by future analysts, so every word must earn its place."
            f"{get_language_instruction('the entire reflection')}"
        )

    def reflect_on_final_decision(
        self,
        final_decision: str,
        raw_return: float,
        alpha_return: float,
        benchmark_name: str = "SPY",
        *,
        ticker: str | None = None,
        holding_days: int | None = None,
        observation_start: str | None = None,
        observation_end: str | None = None,
    ) -> str:
        """Single reflection call on the final trade decision with outcome context.

        Used by Phase B deferred reflection. The final_trade_decision already
        synthesises all analyst insights, so no separate market context is needed.
        ``benchmark_name`` is the label used for the alpha line (e.g. ``"SPY"``
        for US tickers, ``"^N225"`` for ``.T`` listings); defaults to SPY for
        callers that haven't been updated to thread the benchmark through.
        Window metadata is keyword-only and optional for compatibility; the
        graph lifecycle always supplies it.
        """
        context_lines = []
        if ticker:
            context_lines.append(f"Instrument: {ticker}")
        if holding_days is not None and observation_start and observation_end:
            context_lines.append(
                "Observation window: "
                f"{observation_start} to {observation_end} "
                f"({holding_days} completed aligned trading sessions)"
            )
        context = "\n".join(context_lines)
        if context:
            context += "\n"
        messages = [
            ("system", self.log_reflection_prompt),
            (
                "human",
                (
                    f"{context}Raw return: {raw_return:+.1%}\n"
                    f"Alpha vs {benchmark_name}: {alpha_return:+.1%}\n\n"
                    f"Final Decision:\n{final_decision}"
                ),
            ),
        ]
        reflection = self.quick_thinking_llm.invoke(messages).content
        if holding_days is not None and observation_start and observation_end:
            # Persist exact dates independently of whether the LLM repeats them.
            # The compact prefix is language-neutral, so it does not conflict
            # with the configured prose output language.
            observation = (
                f"[{observation_start} \u2192 {observation_end} | {holding_days}d]"
            )
            return f"{observation}\n{reflection}"
        return reflection
