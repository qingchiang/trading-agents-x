"""Typed model connections: identity is independent of vendor presets."""

from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ConnectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)


class EndpointTransport(ConnectionModel):
    kind: Literal["chat_completions", "responses", "anthropic", "google"] = "chat_completions"
    base_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
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
                raise ValueError("Use an HTTP(S) address without credentials, query or fragment")
            return value.rstrip("/")
        return value


class AzureTransport(EndpointTransport):
    kind: Literal["azure"] = "azure"
    deployment: str | None = None
    api_version: str | None = None


class BedrockTransport(ConnectionModel):
    kind: Literal["bedrock"] = "bedrock"
    region: str = "us-west-2"
    auth_mode: Literal["system", "static", "bearer"] = "system"
    aws_profile: str | None = None


Transport = Annotated[
    EndpointTransport | AzureTransport | BedrockTransport, Field(discriminator="kind")
]


class ModelConnection(ConnectionModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    deleted: bool = False
    preset: str | None = None
    compatibility: str = "openai_compatible"
    discovery: Literal[
        "openai_compatible", "anthropic", "google", "ollama", "bedrock", "custom"
    ] = "openai_compatible"
    key_required: bool = True
    transport: Transport
    template: dict = Field(default_factory=dict)
    reasoning_defaults: dict[str, str | None] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter a connection name")
        return value

    @field_validator("reasoning_defaults")
    @classmethod
    def known_reasoning_fields(cls, value):
        if set(value) - {"openai_reasoning_effort", "google_thinking_level", "anthropic_effort"}:
            raise ValueError("Unknown reasoning default")
        return value

    def execution_identity(self):
        return {
            "transport": self.transport.model_dump(),
            "compatibility": self.compatibility,
            "key_required": self.key_required,
        }

    def credential_fields(self):
        if self.transport.kind == "bedrock":
            return ("access_key_id", "secret_access_key", "session_token", "bearer_token")
        return ("api_key",)

    def missing_fields(self, credentials):
        missing = []
        if self.transport.kind != "bedrock":
            if not self.transport.base_url:
                missing.append("base_url")
            if self.key_required and not credentials.get("api_key"):
                missing.append("api_key")
            if self.transport.kind == "azure" and not self.transport.api_version:
                missing.append("api_version")
        elif self.transport.auth_mode == "static":
            missing.extend(
                key for key in ("access_key_id", "secret_access_key") if not credentials.get(key)
            )
        elif self.transport.auth_mode == "bearer" and not credentials.get("bearer_token"):
            missing.append("bearer_token")
        return missing


class ConnectionView(ConnectionModel):
    connection: ModelConnection
    credentials: dict[str, bool]
    missing_fields: list[str]
    selectable: bool
    unavailable_reason: str | None = None
    reasoning_efforts: list[str] = Field(default_factory=list)


class ConnectionChange(ConnectionModel):
    action: Literal["create", "update", "delete", "reset"]
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    preset: str | None = None
    enabled: bool | None = None
    transport: Transport | None = None
    compatibility: str | None = None
    discovery: (
        Literal["openai_compatible", "anthropic", "google", "ollama", "bedrock", "custom"] | None
    ) = None
    key_required: bool | None = None
    reasoning_defaults: dict[str, str | None] | None = None
    credentials: dict[str, SecretStr | None] = Field(default_factory=dict, repr=False)


class ModelBinding(ConnectionModel):
    connection: ModelConnection
    model: str = Field(min_length=1)
    reasoning_effort: str | None = None


def legacy_connection_id(provider):
    return str(uuid5(NAMESPACE_URL, "tradingagents:legacy-provider:" + provider))


def credential_name(connection_id, field):
    return f"connection:{connection_id}:{field}"


def preset_connection(provider, *, identity=None, name=None, legacy=None, defaults=None):
    from tradingagents.llm_clients.provider_presets import _is_native_openai_base_url
    from tradingagents.llm_clients.provider_registry import get_provider_definition

    definition = get_provider_definition(provider)
    if definition is None:
        raise ValueError("Unknown connection preset")
    legacy = legacy or {}
    url = legacy.get("base_url") or definition.default_base_url
    kind = (
        provider if provider in {"anthropic", "google", "azure", "bedrock"} else "chat_completions"
    )
    if provider == "openai" and _is_native_openai_base_url(url):
        kind = "responses"
    if kind == "bedrock":
        transport = {
            "kind": kind,
            **{k: v for k, v in legacy.items() if k in {"region", "auth_mode", "aws_profile"}},
        }
    else:
        transport = {"kind": kind, "base_url": url}
        if kind == "azure":
            transport.update({k: legacy.get(k) for k in ("deployment", "api_version")})
    payload = {
        "id": identity or legacy_connection_id(provider),
        "name": name or definition.label,
        "preset": provider,
        "compatibility": provider,
        "discovery": definition.adapter,
        "key_required": definition.api_key_required,
        "transport": transport,
        "reasoning_defaults": defaults or {},
    }
    result = ModelConnection.model_validate(payload)
    return result.model_copy(
        update={
            "template": {
                k: result.model_dump()[k]
                for k in (
                    "transport",
                    "compatibility",
                    "discovery",
                    "key_required",
                    "reasoning_defaults",
                )
            }
        }
    )


def connection_view(connection, credentials):
    from tradingagents.llm_clients.reasoning_effort import provider_effort_levels

    missing = connection.missing_fields(credentials)
    import importlib.util

    reason = (
        "deleted"
        if connection.deleted
        else "disabled"
        if not connection.enabled
        else "missing_fields"
        if missing
        else None
    )
    if (
        reason is None
        and connection.transport.kind == "bedrock"
        and importlib.util.find_spec("langchain_aws") is None
    ):
        reason = "missing_optional_dependency"
    return ConnectionView(
        connection=connection,
        credentials={key: bool(credentials.get(key)) for key in connection.credential_fields()},
        missing_fields=missing,
        selectable=reason is None,
        unavailable_reason=reason,
        reasoning_efforts=["provider_default", *provider_effort_levels(connection.compatibility)],
    )


def legacy_credential_fields():
    from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV

    fields = {
        name: (provider, "api_key") for provider, name in PROVIDER_API_KEY_ENV.items() if name
    }
    fields.update(
        {
            "AWS_ACCESS_KEY_ID": ("bedrock", "access_key_id"),
            "AWS_SECRET_ACCESS_KEY": ("bedrock", "secret_access_key"),
            "AWS_SESSION_TOKEN": ("bedrock", "session_token"),
            "AWS_BEARER_TOKEN_BEDROCK": ("bedrock", "bearer_token"),
        }
    )
    return fields
