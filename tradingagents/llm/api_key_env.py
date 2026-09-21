"""Canonical SDK credential names used by connection scopes and explicit import."""

from __future__ import annotations

from tradingagents.llm.provider_presets import OPENAI_COMPATIBLE_PROVIDERS

PROVIDER_API_KEY_ENV: dict[str, str | None] = {
    **{name: spec.api_key_env for name, spec in OPENAI_COMPATIBLE_PROVIDERS.items()},
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "bedrock": None,
}


def get_api_key_env(provider: str) -> str | None:
    """Return the env var name for `provider`'s API key, or None if not applicable.

    Unknown providers also return None — callers should treat that as
    "no key check possible" rather than as "no key required".
    """
    return PROVIDER_API_KEY_ENV.get(provider.lower())
