"""Typed configuration documents shared by the application and HTTP interface."""

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from tradingagents.configuration.defaults import DEFAULT_CONFIG as D
from tradingagents.domain.model_selection import ModelSelection, RoleSelections
from tradingagents.llm.models import ConnectionChange, ConnectionView, ModelConnection


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ConfigurationValues(ConfigurationModel):
    profile: Literal["fast", "standard", "deep"] = "standard"
    analysts: list[Literal["market", "social", "news", "fundamentals"]] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"], min_length=1
    )
    models: RoleSelections = Field(default_factory=lambda: RoleSelections(
        quick=ModelSelection(connection_id="default", model="gpt-5.4-mini"),
        deep=ModelSelection(connection_id="default", model="gpt-5.5"),
    ))
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

    @field_validator("*", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value, info):
        if isinstance(value, bool) and info.field_name not in {"news_cache_enabled"}:
            raise ValueError("Use a value of the indicated type, not a boolean")
        return value

    @field_validator("output_language")
    @classmethod
    def language(cls, value):
        from tradingagents.domain.common import normalize_report_language, report_language_value

        return report_language_value(normalize_report_language(value))

    @field_validator("analysts")
    @classmethod
    def unique_analysts(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Select each analyst once")
        return value


class ConfigurationPatch(ConfigurationModel):
    connection_changes: list[ConnectionChange] = Field(default_factory=list)
    revision: int = Field(ge=0)
    values: ConfigurationValues = Field(default_factory=ConfigurationValues)
    reset_fields: list[str] = Field(default_factory=list)
    credentials: dict[str, SecretStr | None] = Field(default_factory=dict, repr=False)


class ConfigurationView(ConfigurationModel):
    connections: dict[str, ConnectionView] = Field(default_factory=dict)
    initialized: bool
    revision: int
    values: ConfigurationValues
    sources: dict[str, Literal["database", "default"]]
    credentials: dict[str, bool]
    deployment: dict[str, str | int | float | bool]


class CredentialRequest(ConfigurationModel):
    name: str
    connection_id: str | None = None


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
    connection_targets: dict[str, str] = Field(default_factory=dict)
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
    kind: Literal["text", "number", "boolean", "list", "choice", "routes", "models"]
    default: Any = None
    nullable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    options: list[str] = Field(default_factory=list)
    option_labels: dict[str, dict[str, str]] = Field(default_factory=dict)
    env_names: list[str] = Field(default_factory=list)


class CredentialMetadata(ConfigurationModel):
    label: str
    description: dict[str, str]


class ConfigurationSchema(ConfigurationModel):
    group_descriptions: dict[str, dict[str, str]] = Field(default_factory=dict)
    credential_metadata: dict[str, CredentialMetadata] = Field(default_factory=dict)
    presets: dict[str, ModelConnection] = Field(default_factory=dict)
    fields: list[ConfigurationField]
    providers: dict[str, str]
    credential_owners: dict[str, str]
    route_options: dict[str, list[str]]
    tool_options: dict[str, list[str]]
