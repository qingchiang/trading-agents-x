"""Tests for DeepSeekChatOpenAI thinking-mode behaviour.

Two pieces verified:

1. ``reasoning_content`` is captured on receive into the AIMessage's
   ``additional_kwargs`` and re-attached on send so DeepSeek's API
   sees the same value across turns.
2. ``with_structured_output`` consults the capability table: V4 forces the
   schema tool, while the legacy reasoner endpoint suppresses unsupported
   ``tool_choice``.
"""

import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompt_values import ChatPromptValue
from pydantic import BaseModel

from tradingagents.llm_clients.openai_client import (
    DeepSeekChatOpenAI,
    NormalizedChatOpenAI,
    _input_to_messages,
)

# ---------------------------------------------------------------------------
# _input_to_messages — the helper that handles list / ChatPromptValue / other
# (Gemini bot review note: non-list inputs must also work)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInputToMessages:
    def test_list_input_returned_as_is(self):
        msgs = [HumanMessage(content="hi")]
        assert _input_to_messages(msgs) is msgs

    def test_chat_prompt_value_unwrapped(self):
        msgs = [HumanMessage(content="hi")]
        prompt_value = ChatPromptValue(messages=msgs)
        assert _input_to_messages(prompt_value) == msgs

    def test_string_input_yields_empty_list(self):
        # A bare string isn't a message-bearing input; the caller's normal
        # langchain conversion happens upstream of _get_request_payload.
        assert _input_to_messages("hello") == []


# ---------------------------------------------------------------------------
# Reasoning content propagation across turns
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeepSeekReasoningContent:
    def _client(self):
        os.environ.setdefault("DEEPSEEK_API_KEY", "placeholder")
        return DeepSeekChatOpenAI(
            model="deepseek-v4-flash",
            api_key="placeholder",
            base_url="https://api.deepseek.com",
        )

    def test_capture_on_receive(self):
        """When the response carries reasoning_content, it lands on the
        AIMessage's additional_kwargs so the next turn can echo it back."""
        client = self._client()
        result = client._create_chat_result(
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Plan: buy NVDA.",
                            "reasoning_content": "Step 1: trend is up. Step 2: ...",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
        ai = result.generations[0].message
        assert ai.additional_kwargs["reasoning_content"] == "Step 1: trend is up. Step 2: ..."

    def test_propagate_on_send(self):
        """When an outgoing AIMessage carries reasoning_content, the request
        payload echoes it on the corresponding message dict."""
        client = self._client()
        prior = AIMessage(
            content="Plan",
            additional_kwargs={"reasoning_content": "weighed bull case"},
        )
        new_user = HumanMessage(content="Refine.")
        payload = client._get_request_payload([prior, new_user])
        # Find the assistant message in the payload
        assistant_dicts = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert assistant_dicts, "assistant message missing from outgoing payload"
        assert assistant_dicts[0]["reasoning_content"] == "weighed bull case"

    def test_propagate_through_chat_prompt_value(self):
        """Gemini bot review note: non-list inputs (ChatPromptValue) must
        also propagate reasoning_content."""
        client = self._client()
        prior = AIMessage(
            content="Plan",
            additional_kwargs={"reasoning_content": "weighed bull case"},
        )
        prompt_value = ChatPromptValue(messages=[prior, HumanMessage(content="Refine.")])
        payload = client._get_request_payload(prompt_value)
        assistant_dicts = [m for m in payload["messages"] if m.get("role") == "assistant"]
        assert assistant_dicts[0]["reasoning_content"] == "weighed bull case"

    def test_reasoning_effort_is_sent_as_top_level_parameter(self):
        client = DeepSeekChatOpenAI(
            model="deepseek-v4-pro",
            api_key="placeholder",
            base_url="https://api.deepseek.com",
            reasoning_effort="max",
        )
        payload = client._get_request_payload([HumanMessage(content="Analyze.")])
        assert payload["reasoning_effort"] == "max"
        assert "thinking" not in payload.get("extra_body", {})


# ---------------------------------------------------------------------------
# Capability-driven structured output
# ---------------------------------------------------------------------------


def _bound_kwargs(runnable):
    """Extract bind() kwargs from a with_structured_output result."""
    first = runnable.steps[0] if hasattr(runnable, "steps") else runnable
    return getattr(first, "kwargs", {})


@pytest.mark.unit
class TestStructuredOutputCapabilityDispatch:
    """V4 forces schema tools; the legacy reasoner keeps its compatibility."""

    class _Sample(BaseModel):
        answer: str

    def _client(self, model):
        return DeepSeekChatOpenAI(
            model=model, api_key="placeholder", base_url="https://api.deepseek.com",
        )

    def test_chat_sends_tool_choice(self):
        bound = self._client("deepseek-chat").with_structured_output(self._Sample)
        assert _bound_kwargs(bound).get("tool_choice") is not None

    def test_reasoner_suppresses_tool_choice(self):
        bound = self._client("deepseek-reasoner").with_structured_output(self._Sample)
        # tool_choice is either absent or explicitly None — both are valid
        # signals that langchain's bind_tools will skip the parameter.
        assert _bound_kwargs(bound).get("tool_choice") in (None, ...) or \
            "tool_choice" not in _bound_kwargs(bound)

    def test_v4_flash_forces_tool_choice(self):
        bound = self._client("deepseek-v4-flash").with_structured_output(self._Sample)
        assert _bound_kwargs(bound).get("tool_choice") is not None

    def test_v4_pro_forces_tool_choice(self):
        bound = self._client("deepseek-v4-pro").with_structured_output(self._Sample)
        assert _bound_kwargs(bound).get("tool_choice") is not None

    def test_v4_json_mode_sets_response_format(self):
        bound = self._client("deepseek-v4-flash").with_structured_output(
            self._Sample,
            method="json_mode",
        )
        assert _bound_kwargs(bound)["response_format"] == {
            "type": "json_object"
        }

    def test_future_v_variant_via_regex(self):
        """Forward-compat: unknown deepseek-v\\d-* IDs inherit V4 support."""
        bound = self._client("deepseek-v5-hypothetical").with_structured_output(self._Sample)
        assert _bound_kwargs(bound).get("tool_choice") is not None

    def test_schema_is_still_bound_as_tool(self):
        """tool_choice is suppressed, but the schema is still bound as a tool —
        exactly matching DeepSeek's official tool-calling examples."""
        bound = self._client("deepseek-reasoner").with_structured_output(self._Sample)
        kwargs = _bound_kwargs(bound)
        tools = kwargs.get("tools", [])
        assert any(
            t.get("function", {}).get("name") == "_Sample" for t in tools
        ), f"schema not bound as a tool: {tools}"


# ---------------------------------------------------------------------------
# Live API: structured output round-trips against the real DeepSeek backend
# ---------------------------------------------------------------------------


def _live_deepseek_enabled():
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    return (
        os.environ.get("RUN_LIVE_LLM_TESTS") == "1"
        and bool(key)
        and key != "placeholder"
    )


@pytest.mark.integration
@pytest.mark.live_llm
class TestDeepSeekLiveStructuredOutput:
    """Explicit opt-in probes for V4 schema tools and JSON Output."""

    class _Pick(BaseModel):
        action: str
        confidence: float

    @pytest.mark.parametrize(
        ("model", "method"),
        (
            ("deepseek-v4-flash", "function_calling"),
            ("deepseek-v4-flash", "json_mode"),
            ("deepseek-v4-pro", "function_calling"),
            ("deepseek-v4-pro", "json_mode"),
        ),
    )
    def test_v4_returns_structured_output(self, model, method):
        if not _live_deepseek_enabled():
            pytest.skip(
                "Set RUN_LIVE_LLM_TESTS=1 and export DEEPSEEK_API_KEY to run live"
            )
        client = DeepSeekChatOpenAI(
            model=model,
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
            timeout=60,
        )
        bound = client.with_structured_output(
            self._Pick,
            method=method,
        )
        result = bound.invoke(
            "Pick BUY or SELL or HOLD for a tech stock with strong earnings. "
            "Confidence is a float between 0 and 1. Return a JSON object with "
            "exactly the action and confidence fields."
        )
        assert isinstance(result, self._Pick)
        assert result.action in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# Base class isolation: NormalizedChatOpenAI does NOT have DeepSeek behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseClassIsolation:
    def test_normalized_does_not_propagate_reasoning_content(self):
        """The general-purpose NormalizedChatOpenAI must not carry
        DeepSeek-specific behaviour. Only the subclass does."""
        assert not hasattr(NormalizedChatOpenAI, "_get_request_payload") or (
            NormalizedChatOpenAI._get_request_payload
            is NormalizedChatOpenAI.__bases__[0]._get_request_payload
        )
