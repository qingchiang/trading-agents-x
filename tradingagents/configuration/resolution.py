"""Resolution."""

from copy import deepcopy

from tradingagents.configuration.errors import ConfigurationError
from tradingagents.configuration.models import (
    ProviderConnection,
)


def effective_connection(values, provider):
    from tradingagents.llm.provider_registry import PROVIDER_REGISTRY

    if provider not in PROVIDER_REGISTRY:
        raise ConfigurationError("Unknown model provider")
    connection = deepcopy(values.providers.get(provider, ProviderConnection()).model_dump())
    connection["base_url"] = connection["base_url"] or PROVIDER_REGISTRY[provider].default_base_url
    if connection["base_url"]:
        connection["base_url"] = connection["base_url"].rstrip("/")
    return connection



def validate_values(values):
    from tradingagents.data.interface import validate_market_routing
    from tradingagents.llm.provider_registry import PROVIDER_REGISTRY
    from tradingagents.llm.reasoning_effort import resolve_reasoning_effort

    if values.llm_provider not in PROVIDER_REGISTRY or set(values.providers) - set(
        PROVIDER_REGISTRY
    ):
        raise ConfigurationError("Unknown model provider", fields=["llm_provider", "providers"])
    config = values.model_dump()
    try:
        validate_market_routing(config)
    except ValueError as exc:
        raise ConfigurationError(
            "Invalid data route; choose sources that serve each method",
            fields=["data_vendors", "data_vendors_by_market", "tool_vendors"],
        ) from exc
    for role in ("quick", "deep"):
        if getattr(values, f"{role}_connection_id"):
            continue
        try:
            resolve_reasoning_effort(config, role)
        except ValueError as exc:
            raise ConfigurationError(
                "Unsupported reasoning setting for the selected model",
                fields=[
                    f"{role}_reasoning_effort",
                    "openai_reasoning_effort",
                    "google_thinking_level",
                    "anthropic_effort",
                ],
            ) from exc



def credential_owners() -> dict[str, str]:
    from tradingagents.llm.api_key_env import PROVIDER_API_KEY_ENV

    return {
        **{name: provider for provider, name in PROVIDER_API_KEY_ENV.items() if name},
        "ALPHA_VANTAGE_API_KEY": "alpha_vantage",
        "FRED_API_KEY": "fred",
        "ESTAT_APP_ID": "estat",
        "JQUANTS_API_KEY": "jquants",
        "EDINET_API_KEY": "edinet",
        "AWS_BEARER_TOKEN_BEDROCK": "bedrock",
        "AWS_ACCESS_KEY_ID": "bedrock",
        "AWS_SECRET_ACCESS_KEY": "bedrock",
        "AWS_SESSION_TOKEN": "bedrock",
    }
