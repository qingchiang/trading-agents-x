"""Typed configuration documents shared by the application and HTTP interface."""

from copy import deepcopy
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from tradingagents.default_config import DEFAULT_CONFIG as D


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ProviderConnection(ConfigurationModel):
    base_url: str | None = None
    deployment: str | None = None
    api_version: str | None = None
    region: str = "us-west-2"
    auth_mode: Literal["bearer", "system", "static"] = "system"
    aws_profile: str | None = None

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        if value is not None:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Use an HTTP(S) service address without embedded credentials")
        return value.rstrip("/") if value else None


class ConfigurationValues(ConfigurationModel):
    profile: Literal["fast", "standard", "deep"] = "standard"
    analysts: list[Literal["market", "social", "news", "fundamentals"]] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"], min_length=1
    )
    llm_provider: str = D["llm_provider"]
    quick_think_llm: str = Field(default=D["quick_think_llm"], min_length=1)
    deep_think_llm: str = Field(default=D["deep_think_llm"], min_length=1)
    quick_reasoning_effort: str | None = D["quick_reasoning_effort"]
    deep_reasoning_effort: str | None = D["deep_reasoning_effort"]
    google_thinking_level: str | None = D["google_thinking_level"]
    openai_reasoning_effort: str | None = D["openai_reasoning_effort"]
    anthropic_effort: str | None = D["anthropic_effort"]
    temperature: float | None = Field(default=D["temperature"], allow_inf_nan=False)
    llm_max_retries: int | None = Field(default=D["llm_max_retries"], ge=0)
    output_language: str = Field(default="en", min_length=1)
    news_article_limit: int = Field(default=D["news_article_limit"], ge=1)
    yahoo_news_candidate_limit: int = Field(default=D["yahoo_news_candidate_limit"], ge=1, le=200)
    cn_news_candidate_limit: int = Field(default=D["cn_news_candidate_limit"], ge=1, le=100)
    sentiment_filing_limit: int = Field(default=D["sentiment_filing_limit"], ge=1)
    ticker_news_lookback_days: int = Field(default=D["ticker_news_lookback_days"], ge=0)
    social_lookback_days: int = Field(default=D["social_lookback_days"], ge=0)
    global_news_article_limit: int = Field(default=D["global_news_article_limit"], ge=1)
    global_news_candidate_limit: int = Field(default=D["global_news_candidate_limit"], ge=1)
    global_news_query_limit: int = Field(default=D["global_news_query_limit"], ge=1, le=5)
    global_news_lookback_days: int = Field(default=D["global_news_lookback_days"], ge=0)
    global_news_queries: list[str] = Field(default_factory=lambda: list(D["global_news_queries"]))
    news_cache_enabled: bool = D["news_cache_enabled"]
    news_cache_refresh_seconds: int = Field(default=D["news_cache_refresh_seconds"], ge=0)
    news_cache_retention_days: int = Field(default=D["news_cache_retention_days"], ge=1)
    news_cache_scope_limit: int = Field(default=D["news_cache_scope_limit"], ge=1)
    news_cache_total_limit: int = Field(default=D["news_cache_total_limit"], ge=1)
    trash_retention_days: int = Field(default=30, ge=0)
    data_vendors: dict[str, str] = Field(default_factory=lambda: deepcopy(D["data_vendors"]))
    tool_vendors: dict[str, str] = Field(default_factory=dict)
    data_vendors_by_market: dict[str, dict[str, str]] = Field(
        default_factory=lambda: deepcopy(D["data_vendors_by_market"])
    )
    providers: dict[str, ProviderConnection] = Field(default_factory=dict)

    @field_validator("*", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value, info):
        if isinstance(value, bool) and info.field_name not in {"news_cache_enabled"}:
            raise ValueError("Use a value of the indicated type, not a boolean")
        return value

    @field_validator("output_language")
    @classmethod
    def language(cls, value):
        from .contracts import normalize_report_language, report_language_value

        return report_language_value(normalize_report_language(value))

    @field_validator("analysts")
    @classmethod
    def unique_analysts(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Select each analyst once")
        return value


class ConfigurationPatch(ConfigurationModel):
    revision: int = Field(ge=0)
    values: ConfigurationValues = Field(default_factory=ConfigurationValues)
    reset_fields: list[str] = Field(default_factory=list)
    credentials: dict[str, SecretStr | None] = Field(default_factory=dict, repr=False)


class ConfigurationView(ConfigurationModel):
    initialized: bool
    revision: int
    values: ConfigurationValues
    sources: dict[str, Literal["database", "default"]]
    credentials: dict[str, bool]
    deployment: dict[str, str | int | float | bool]


class CredentialRequest(ConfigurationModel):
    name: str


class CredentialView(ConfigurationModel):
    value: str | None


class ImportRequest(ConfigurationModel):
    primary: SecretStr | None = None
    enterprise: SecretStr | None = None
    exclude: list[str] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    fingerprint: str | None = None
    use_defaults: bool = False


class ImportIssue(ConfigurationModel):
    name: str
    message: str


class ImportPreview(ConfigurationModel):
    revision: int
    fingerprint: str
    values: dict[str, Any]
    credentials: dict[str, bool]
    issues: list[ImportIssue]
    conflicts: list[str]


class ConfigurationField(ConfigurationModel):
    key: str
    group: str
    label: dict[str, str]
    description: dict[str, str]
    kind: Literal["text", "number", "boolean", "list", "choice", "routes", "providers"]
    default: Any = None
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    options: list[str] = Field(default_factory=list)
    env_names: list[str] = Field(default_factory=list)


class ConfigurationSchema(ConfigurationModel):
    fields: list[ConfigurationField]
    providers: dict[str, str]
    provider_defaults: dict[str, ProviderConnection]
    credential_owners: dict[str, str]
    route_options: dict[str, list[str]]
    tool_options: dict[str, list[str]]
