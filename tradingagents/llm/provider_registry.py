"""Provider metadata shared by model discovery and Web capabilities.

The registry deliberately stores only non-sensitive metadata. API key values
come from a resolved credential snapshot and are never returned by this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from tradingagents.llm.api_key_env import PROVIDER_API_KEY_ENV
from tradingagents.llm.provider_presets import OPENAI_COMPATIBLE_PROVIDERS

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


def _openai_compatible_definitions() -> dict[str, ProviderDefinition]:
    definitions: dict[str, ProviderDefinition] = {}
    for name, spec in OPENAI_COMPATIBLE_PROVIDERS.items():
        definitions[name] = ProviderDefinition(
            name=name,
            label=spec.label,
            adapter="ollama" if name == "ollama" else "openai_compatible",
            api_key_env=spec.api_key_env,
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
            default_base_url="https://api.anthropic.com",
        ),
        "google": ProviderDefinition(
            name="google",
            label="Google Gemini",
            adapter="google",
            api_key_env=PROVIDER_API_KEY_ENV["google"],
            api_key_required=True,
            default_base_url="https://generativelanguage.googleapis.com",
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
