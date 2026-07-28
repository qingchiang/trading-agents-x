"""Helpers for one-shot structured agent output with a graceful fallback.

The Sentiment Analyst follows this pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

JSON-mode providers also receive an explicit schema contract without leaking
that contract into the free-text fallback prompt.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Schema-only structured output binds exactly one tool (the schema itself), so a
# model that reaches for a search tool emits an unknown tool call and the whole
# structured attempt is discarded for a free-text retry. Agents on this path
# state the constraint explicitly rather than relying on the binding alone
# (#1130).
NO_EXTERNAL_TOOLS = (
    "Use only the evidence provided in this prompt. Do not call external tools "
    "or search the web; if something is missing, say so explicitly."
)


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Any | None:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    kwargs: dict[str, Any] = {}
    max_tokens = getattr(llm, "structured_output_max_tokens", None)
    if (
        isinstance(max_tokens, int)
        and not isinstance(max_tokens, bool)
        and max_tokens > 0
    ):
        kwargs["max_tokens"] = max_tokens
    try:
        return llm.with_structured_output(schema, **kwargs)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def structured_prompt_for(
    llm: Any,
    schema: type[T],
    prompt: Any,
) -> Any:
    """Add a JSON Schema contract only when the provider selects JSON mode."""
    if getattr(llm, "preferred_structured_output_method", None) != "json_mode":
        return prompt
    contract = (
        "Return exactly one JSON object and no Markdown, prose, or code fence. "
        "The JSON object must satisfy this JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False, sort_keys=True)}"
    )
    instruction = HumanMessage(content=contract)
    if isinstance(prompt, list):
        return [*prompt, instruction]
    if isinstance(prompt, tuple):
        return (*prompt, instruction)
    return f"{prompt}\n\n{contract}"


def invoke_structured_or_freetext(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    *,
    structured_prompt: Any | None = None,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``structured_prompt`` may add provider-specific output instructions. The
    original ``prompt`` is always forwarded to the free-text fallback.
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(
                prompt if structured_prompt is None else structured_prompt
            )
            if result is None:
                # A thinking model can answer in plain text instead of calling
                # the tool, leaving the parser with nothing to return. Treat it
                # as a structured miss and fall back, with a clear reason.
                raise ValueError("structured output returned no parsed result")
            return render(result)
        except Exception as exc:
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    return response.content
