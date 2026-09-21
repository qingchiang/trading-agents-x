from typing import Any, Self

from google.genai import Client
from google.genai.types import HttpOptions
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import _is_gemini_3_or_later, get_user_agent
from pydantic import SecretStr, model_validator

from tradingagents.credentials import credential
from tradingagents.llm.base_client import BaseLLMClient, normalize_content
from tradingagents.llm.provider_registry import PROVIDER_REGISTRY
from tradingagents.llm.reasoning_effort import RESOLVED_MARKER, resolve_native_reasoning_value
from tradingagents.llm.validators import validate_model


class NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI with normalized content output.

    Gemini 3 models return content as list of typed blocks.
    This normalizes to string for consistent downstream handling.
    """

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        """Construct the SDK without its ambient backend selection.

        The pinned LangChain validator omits vertexai=False when creating its
        Developer API client. Override that seam, retaining its parameter checks
        and Gemini temperature normalization without changing process state.
        """
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be in the range [0.0, 2.0]")
        if "temperature" not in self.model_fields_set and _is_gemini_3_or_later(self.model):
            self.temperature = 1.0
        if self.top_p is not None and not 0 <= self.top_p <= 1:
            raise ValueError("top_p must be in the range [0.0, 1.0]")
        if self.top_k is not None and self.top_k <= 0:
            raise ValueError("top_k must be positive")
        key = self.google_api_key
        key = key.get_secret_value() if isinstance(key, SecretStr) else key
        if not key:
            raise ValueError("Configure the Google credential in Settings")
        headers = self.additional_headers or {}
        self.default_metadata = tuple(headers.items())
        _, user_agent = get_user_agent("ChatGoogleGenerativeAI")
        self.client = Client(
            vertexai=False,
            api_key=key,
            http_options=HttpOptions(
                base_url=self.base_url,
                api_version=self.api_version,
                headers={"user-agent": user_agent, **headers},
                client_args=self.client_args,
                async_client_args=self.client_args,
            ),
        )
        return self

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class GoogleClient(BaseLLMClient):
    """Client for Google Gemini models."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatGoogleGenerativeAI instance."""
        self.warn_if_unknown_model()
        # This transport represents the Developer API, never ambient Vertex AI.
        llm_kwargs = {"model": self.model, "vertexai": False}

        llm_kwargs["base_url"] = self.base_url or PROVIDER_REGISTRY["google"].default_base_url

        for key in ("timeout", "max_retries", "temperature", "callbacks", "http_client", "http_async_client"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # Unified api_key maps to provider-specific google_api_key
        google_api_key = self.kwargs.get("api_key") or self.kwargs.get("google_api_key") or credential("GOOGLE_API_KEY")
        if not google_api_key:
            raise ValueError("Configure the Google credential in Settings")
        if google_api_key:
            llm_kwargs["google_api_key"] = google_api_key

        # Gemini 3.x takes the string ``thinking_level``. Capability validation
        # and the established Pro minimal->low compatibility mapping are shared
        # with the graph/CLI resolver.
        thinking_level = self.kwargs.get("thinking_level")
        if thinking_level:
            if not self.kwargs.get(RESOLVED_MARKER):
                thinking_level = resolve_native_reasoning_value(
                    "google", self.model, thinking_level
                )
            if thinking_level is not None:
                llm_kwargs["thinking_level"] = thinking_level

        return NormalizedChatGoogleGenerativeAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Google."""
        return validate_model("google", self.model)
