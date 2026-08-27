"""Tests for DeepSeekChatOpenAI thinking-mode behaviour.

Two pieces verified:

1. ``reasoning_content`` is captured on receive into the AIMessage's
   ``additional_kwargs`` and re-attached on send so DeepSeek's API
   sees the same value across turns.
2. ``with_structured_output`` consults the capability table: V4 thinking
   models prefer JSON mode, while the legacy reasoner endpoint binds an
   unforced schema tool.
"""

import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompt_values import ChatPromptValue
from pydantic import BaseModel

from tradingagents.application.contracts import ArtifactGenerationMethod
from tradingagents.graph.structured_output import StructuredOutputRunner
from tradingagents.llm_clients.factory import create_llm_client
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

    def test_flash_low_effort_is_sent_as_top_level_parameter(self):
        client = DeepSeekChatOpenAI(
            model="deepseek-v4-flash",
            api_key="placeholder",
            base_url="https://api.deepseek.com",
            reasoning_effort="low",
        )
        payload = client._get_request_payload([HumanMessage(content="Analyze.")])
        assert payload["reasoning_effort"] == "low"

    def test_chat_completions_uses_deepseek_token_budget_field(self):
        client = DeepSeekChatOpenAI(
            model="deepseek-v4-flash",
            api_key="placeholder",
            base_url="https://api.deepseek.com",
        )

        payload = client._get_request_payload(
            [HumanMessage(content="Serialize the result.")],
            max_tokens=16_384,
        )

        assert payload["max_tokens"] == 16_384
        assert "max_completion_tokens" not in payload


# ---------------------------------------------------------------------------
# Capability-driven structured output
# ---------------------------------------------------------------------------


def _bound_kwargs(runnable):
    """Extract bind() kwargs from a with_structured_output result."""
    first = runnable.steps[0] if hasattr(runnable, "steps") else runnable
    return getattr(first, "kwargs", {})


@pytest.mark.unit
class TestStructuredOutputCapabilityDispatch:
    """V4 prefers JSON mode; legacy DeepSeek compatibility stays isolated."""

    class _Sample(BaseModel):
        answer: str

    def _client(self, model, **kwargs):
        return DeepSeekChatOpenAI(
            model=model, api_key="placeholder", base_url="https://api.deepseek.com",
            **kwargs,
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

    def test_v4_flash_defaults_to_json_mode(self):
        bound = self._client("deepseek-v4-flash").with_structured_output(self._Sample)
        assert _bound_kwargs(bound)["response_format"] == {
            "type": "json_object"
        }

    def test_v4_pro_defaults_to_json_mode(self):
        bound = self._client("deepseek-v4-pro").with_structured_output(self._Sample)
        assert _bound_kwargs(bound)["response_format"] == {
            "type": "json_object"
        }

    def test_v4_explicit_thinking_mode_stays_on_json_mode(self):
        client = self._client(
            "deepseek-v4-pro",
            extra_body={"thinking": {"type": "enabled"}},
        )
        bound = client.with_structured_output(self._Sample)
        assert _bound_kwargs(bound)["response_format"] == {
            "type": "json_object"
        }

    def test_v4_json_mode_sets_response_format(self):
        bound = self._client("deepseek-v4-flash").with_structured_output(
            self._Sample,
            method="json_mode",
        )
        assert _bound_kwargs(bound)["response_format"] == {
            "type": "json_object"
        }

    @pytest.mark.parametrize(
        "model",
        ("deepseek-v4-flash", "deepseek-v4-pro"),
    )
    def test_v4_non_thinking_uses_forced_schema_tool(self, model):
        client = self._client(
            model,
            extra_body={"thinking": {"type": "disabled"}},
        )
        bound = client.with_structured_output(self._Sample)
        kwargs = _bound_kwargs(bound)
        assert kwargs.get("tool_choice") is not None
        assert "response_format" not in kwargs
        assert client.preferred_structured_output_method == "function_calling"

    def test_future_v_variant_uses_unknown_model_default(self):
        """Future model IDs wait for discovery instead of inheriting V4."""
        bound = self._client("deepseek-v5-hypothetical").with_structured_output(self._Sample)
        kwargs = _bound_kwargs(bound)
        assert kwargs.get("tool_choice") is not None
        assert "response_format" not in kwargs

    def test_v4_exposes_structured_output_safety_ceiling(self):
        client = self._client("deepseek-v4-flash")
        assert client.structured_output_max_tokens == 16_384

    def test_explicit_client_max_tokens_overrides_safety_ceiling(self):
        client = self._client("deepseek-v4-flash", max_tokens=8192)
        assert client.structured_output_max_tokens == 8192

    def test_factory_forwards_non_thinking_mode_and_token_budget(
        self,
        monkeypatch,
    ):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
        client = create_llm_client(
            provider="deepseek",
            model="deepseek-v4-flash",
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=8192,
        ).get_llm()

        assert client.preferred_structured_output_method == "function_calling"
        assert client.structured_output_max_tokens == 8192

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
    """Opt-in V4 probes for thinking JSON and non-thinking forced tools."""

    class _Pick(BaseModel):
        action: str
        confidence: float

    @pytest.mark.parametrize(
        "model",
        (
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ),
    )
    def test_v4_default_returns_structured_output(self, model):
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
        runner = StructuredOutputRunner(
            llm=client,
            schema=self._Pick,
            validator=lambda value: value,
            node="live.pick",
        )
        result = runner.invoke(
            "Pick BUY or SELL or HOLD for a tech stock with strong earnings. "
            "Confidence is a float between 0 and 1.",
            example={"action": "HOLD", "confidence": 0.5},
            allowed_evidence_refs=(),
        )
        assert isinstance(result.value, self._Pick)
        assert result.value.action in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= result.value.confidence <= 1.0
        assert result.generation_method is ArtifactGenerationMethod.JSON_MODE

    @pytest.mark.parametrize(
        "model",
        (
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ),
    )
    def test_v4_non_thinking_forces_schema_tool(self, model):
        if not _live_deepseek_enabled():
            pytest.skip(
                "Set RUN_LIVE_LLM_TESTS=1 and export DEEPSEEK_API_KEY to run live"
            )
        client = DeepSeekChatOpenAI(
            model=model,
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
            timeout=60,
            max_tokens=256,
            extra_body={"thinking": {"type": "disabled"}},
        )
        bound = client.with_structured_output(self._Pick)
        result = bound.invoke(
            "Choose BUY, SELL, or HOLD for a fictional stock. Confidence must "
            "be a float between 0 and 1."
        )
        assert isinstance(result, self._Pick)
        assert result.action in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= result.confidence <= 1.0

    @pytest.mark.parametrize(
        "model",
        (
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ),
    )
    def test_v4_non_thinking_accepts_required_tool_choice(self, model):
        if not _live_deepseek_enabled():
            pytest.skip(
                "Set RUN_LIVE_LLM_TESTS=1 and export DEEPSEEK_API_KEY to run live"
            )
        client = DeepSeekChatOpenAI(
            model=model,
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
            timeout=60,
            max_tokens=256,
            extra_body={"thinking": {"type": "disabled"}},
        )
        bound = client.bind_tools([self._Pick], tool_choice="required")
        result = bound.invoke(
            "Call the supplied tool with action HOLD and confidence 0.5."
        )
        assert result.tool_calls
        assert result.tool_calls[0]["name"] == "_Pick"


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
