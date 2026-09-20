from typing import Any

from langchain_anthropic import ChatAnthropic

from tradingagents.credentials import credential

from .base_client import BaseLLMClient, normalize_content
from .provider_registry import PROVIDER_REGISTRY
from .reasoning_effort import RESOLVED_MARKER, resolve_native_reasoning_value
from .validators import validate_model

_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "max_tokens", "temperature",
    "callbacks", "http_client", "http_async_client", "effort",
)

class NormalizedChatAnthropic(ChatAnthropic):
    """ChatAnthropic with normalized content output.

    Claude models with extended thinking or tool use return content as a
    list of typed blocks. This normalizes to string for consistent
    downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class AnthropicClient(BaseLLMClient):
    """Client for Anthropic Claude models."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatAnthropic instance."""
        self.warn_if_unknown_model()
        key = self.kwargs.get("api_key") or credential("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("Configure the Anthropic credential in Settings")
        llm_kwargs = {"model": self.model, "api_key": key}

        llm_kwargs["base_url"] = self.base_url or PROVIDER_REGISTRY["anthropic"].default_base_url

        for key in _PASSTHROUGH_KWARGS:
            if key not in self.kwargs:
                continue
            value = self.kwargs[key]
            if key == "effort" and not self.kwargs.get(RESOLVED_MARKER):
                value = resolve_native_reasoning_value(
                    "anthropic", self.model, value
                )
                if value is None:
                    continue
            llm_kwargs[key] = value

        return NormalizedChatAnthropic(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Anthropic."""
        return validate_model("anthropic", self.model)
