"""Provider metadata shared by model discovery and Web capabilities.

The registry deliberately stores only non-sensitive metadata. API key values
remain in the process environment and are never returned by this module.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from tradingagents.application.settings import AppSettings

from .api_key_env import PROVIDER_API_KEY_ENV
from .openai_client import OPENAI_COMPATIBLE_PROVIDERS

DiscoveryAdapter = Literal[
    "openai_compatible",
    "anthropic",
    "google",
    "ollama",
    "bedrock",
    "custom",
]


@dataclass(frozen=True)
class ProviderDefinition:
    """Non-sensitive provider configuration and discovery policy."""

    name: str
    label: str
    adapter: DiscoveryAdapter
    api_key_env: str | None
    api_key_required: bool
    default_base_url: str | None = None
    base_url_env: str | None = None
    base_url_required: bool = False
    required_env: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderAvailability:
    """Safe provider status exposed to local Web clients."""

    configured: bool
    selectable: bool
    api_key_configured: bool | None
    reason: str | None = None


def _openai_compatible_definitions() -> dict[str, ProviderDefinition]:
    labels = {
        "openai": "OpenAI",
        "xai": "xAI",
        "deepseek": "DeepSeek",
        "qwen": "Qwen (International)",
        "qwen-cn": "Qwen (China)",
        "glm": "Z.AI GLM",
        "glm-cn": "BigModel GLM (China)",
        "minimax": "MiniMax (International)",
        "minimax-cn": "MiniMax (China)",
        "openrouter": "OpenRouter",
        "mistral": "Mistral",
        "kimi": "Kimi / Moonshot",
        "groq": "Groq",
        "nvidia": "NVIDIA NIM",
        "ollama": "Ollama",
        "openai_compatible": "OpenAI-compatible",
    }
    definitions: dict[str, ProviderDefinition] = {}
    for name, spec in OPENAI_COMPATIBLE_PROVIDERS.items():
        definitions[name] = ProviderDefinition(
            name=name,
            label=labels[name],
            adapter="ollama" if name == "ollama" else "openai_compatible",
            api_key_env=PROVIDER_API_KEY_ENV.get(name),
            api_key_required=not spec.key_optional,
            default_base_url=(
                "https://api.openai.com/v1"
                if name == "openai" and spec.base_url is None
                else spec.base_url
            ),
            base_url_env=spec.base_url_env,
            base_url_required=spec.require_base_url,
        )
    return definitions


_definitions = _openai_compatible_definitions()
_definitions.update(
    {
        "anthropic": ProviderDefinition(
            name="anthropic",
            label="Anthropic",
            adapter="anthropic",
            api_key_env=PROVIDER_API_KEY_ENV["anthropic"],
            api_key_required=True,
            default_base_url="https://api.anthropic.com/v1",
        ),
        "google": ProviderDefinition(
            name="google",
            label="Google Gemini",
            adapter="google",
            api_key_env=PROVIDER_API_KEY_ENV["google"],
            api_key_required=True,
            default_base_url="https://generativelanguage.googleapis.com/v1beta",
        ),
        "azure": ProviderDefinition(
            name="azure",
            label="Azure OpenAI",
            adapter="custom",
            api_key_env=PROVIDER_API_KEY_ENV["azure"],
            api_key_required=True,
            base_url_env="AZURE_OPENAI_ENDPOINT",
            base_url_required=True,
        ),
        "bedrock": ProviderDefinition(
            name="bedrock",
            label="Amazon Bedrock",
            adapter="bedrock",
            api_key_env=None,
            api_key_required=False,
        ),
    }
)

PROVIDER_REGISTRY: Mapping[str, ProviderDefinition] = MappingProxyType(_definitions)


def get_provider_definition(provider: str) -> ProviderDefinition | None:
    """Return a provider definition without accepting arbitrary endpoints."""
    return PROVIDER_REGISTRY.get(provider.strip().lower())


def resolve_provider_base_url(
    definition: ProviderDefinition,
    settings: AppSettings,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve the endpoint using the same precedence as a configured run."""
    env = os.environ if environ is None else environ
    defaults = settings.default_run_settings
    if definition.name == defaults.llm_provider and defaults.backend_url:
        return defaults.backend_url
    if definition.base_url_env and env.get(definition.base_url_env):
        return env[definition.base_url_env]
    return definition.default_base_url


def provider_availability(
    definition: ProviderDefinition,
    settings: AppSettings,
    environ: Mapping[str, str] | None = None,
) -> ProviderAvailability:
    """Determine whether a provider is configured without exposing credentials."""
    env = os.environ if environ is None else environ
    api_key_configured = (
        None
        if definition.api_key_env is None
        else bool(env.get(definition.api_key_env))
    )
    if definition.api_key_required and not api_key_configured:
        return ProviderAvailability(
            configured=False,
            selectable=False,
            api_key_configured=api_key_configured,
            reason="api_key_missing",
        )
    if definition.base_url_required and not resolve_provider_base_url(
        definition,
        settings,
        env,
    ):
        return ProviderAvailability(
            configured=False,
            selectable=False,
            api_key_configured=api_key_configured,
            reason="endpoint_missing",
        )
    if any(not env.get(name) for name in definition.required_env):
        return ProviderAvailability(
            configured=False,
            selectable=False,
            api_key_configured=api_key_configured,
            reason="configuration_missing",
        )
    if definition.name == "bedrock":
        credential_markers = (
            "AWS_BEARER_TOKEN_BEDROCK",
            "AWS_ACCESS_KEY_ID",
            "AWS_PROFILE",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        )
        has_credentials = any(env.get(name) for name in credential_markers)
        has_adapter = importlib.util.find_spec("langchain_aws") is not None
        if not has_credentials or not has_adapter:
            return ProviderAvailability(
                configured=False,
                selectable=False,
                api_key_configured=None,
                reason=(
                    "optional_dependency_missing"
                    if not has_adapter
                    else "credentials_missing"
                ),
            )
    return ProviderAvailability(
        configured=True,
        selectable=True,
        api_key_configured=api_key_configured,
    )
