from typing import Any

from langchain_openai import AzureChatOpenAI

from tradingagents.credentials import credential

from .base_client import BaseLLMClient, normalize_content
from .reasoning_effort import RESOLVED_MARKER, resolve_native_reasoning_value

_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "reasoning_effort", "temperature",
    "callbacks", "http_client", "http_async_client",
)


class NormalizedAzureChatOpenAI(AzureChatOpenAI):
    """AzureChatOpenAI with normalized content output."""

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class AzureOpenAIClient(BaseLLMClient):
    """Client for Azure OpenAI deployments.

    Receives the endpoint, deployment and API version from the retained
    connection snapshot, and a credential from the execution context.
    """

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured AzureChatOpenAI instance."""
        self.warn_if_unknown_model()

        key = self.kwargs.get("api_key") or credential("AZURE_OPENAI_API_KEY")
        if not key:
            raise ValueError("Configure the Azure credential in Settings")
        connection = self.kwargs.get("connection") or {}
        if not self.base_url or not connection.get("api_version"):
            raise ValueError("Configure Azure endpoint and API version in Settings")
        llm_kwargs = {
            "model": self.model, "api_key": key,
            "azure_endpoint": self.base_url, "base_url": None,
            "azure_deployment": connection.get("deployment") or self.model,
            "api_version": connection["api_version"], "azure_ad_token": None,
        }

        for key in _PASSTHROUGH_KWARGS:
            if key not in self.kwargs:
                continue
            value = self.kwargs[key]
            if key == "reasoning_effort" and not self.kwargs.get(RESOLVED_MARKER):
                value = resolve_native_reasoning_value("azure", self.model, value)
                if value is None:
                    continue
            llm_kwargs[key] = value

        return NormalizedAzureChatOpenAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Azure accepts any deployed model name."""
        return True
