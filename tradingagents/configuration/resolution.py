"""Resolution."""


from tradingagents.configuration.errors import ConfigurationError


def validate_values(values):
    from tradingagents.data.interface import validate_market_routing

    config = values.model_dump()
    try:
        validate_market_routing(config)
    except ValueError as exc:
        raise ConfigurationError(
            "Invalid data route; choose sources that serve each method",
            fields=["data_vendors", "data_vendors_by_market", "tool_vendors"],
        ) from exc


def credential_owners() -> dict[str, str]:

    return {
        "ALPHA_VANTAGE_API_KEY": "alpha_vantage",
        "FRED_API_KEY": "fred",
        "ESTAT_APP_ID": "estat",
        "JQUANTS_API_KEY": "jquants",
        "EDINET_API_KEY": "edinet",
    }
