"""Transactional configuration shared by every application entry point."""

import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps
from hashlib import sha256
from io import StringIO

from dotenv import dotenv_values
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .configuration_models import (
    ConfigurationPatch,
    ConfigurationValues,
    ConfigurationView,
    ImportIssue,
    ImportPreview,
    ImportRequest,
    ProviderConnection,
)
from .contracts import AnalysisRequest, report_language_value
from .database import ConfigurationRecord, CredentialRecord, create_sqlite_engine
from .settings import AppSettings, RunSettings


class ConfigurationError(ValueError):
    code = "invalid_configuration"

    def __init__(self, message, *, fields=()):
        super().__init__(message)
        self.fields = list(fields)


class ConfigurationConflict(ConfigurationError):
    code = "configuration_revision_conflict"


class ConfigurationRequired(ConfigurationError):
    code = "configuration_required"


class ProviderConfigurationChanged(ConfigurationError):
    code = "provider_configuration_changed"


class ConfigurationStore:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.engine = create_sqlite_engine(
            settings.database_path, busy_timeout_ms=settings.busy_timeout_ms
        )

    def read(self) -> ConfigurationView:
        # Preview also works before the application has created/migrated a DB.
        if not self.settings.database_path.exists():
            return self._document({}, set(), 0, False)
        with closing(
            sqlite3.connect(self.settings.database_path.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            connection.execute("BEGIN")
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='application_configuration'"
            ).fetchone():
                return self._document({}, set(), 0, False)
            row = connection.execute(
                "SELECT values_json, revision, initialized FROM application_configuration WHERE id=1"
            ).fetchone()
            names = {
                row[0] for row in connection.execute("SELECT name FROM configuration_credentials")
            }
            return self._document(
                json.loads(row[0]) if row else {},
                names,
                row[1] if row else 0,
                bool(row[2]) if row else False,
            )

    def _view(self, session):
        record = session.get(ConfigurationRecord, 1)
        raw = record.values_json if record else {}
        configured = set(session.scalars(select(CredentialRecord.name)))
        return self._document(
            raw,
            configured,
            record.revision if record else 0,
            record.initialized if record else False,
        )

    def _document(self, raw, configured, revision, initialized):
        return ConfigurationView(
            initialized=initialized,
            revision=revision,
            values=ConfigurationValues.model_validate(raw),
            sources={
                key: "database" if key in raw else "default"
                for key in ConfigurationValues.model_fields
            },
            credentials={name: name in configured for name in credential_owners()},
            deployment={
                key: str(getattr(self.settings, key))
                for key in (
                    "database_path",
                    "data_cache_dir",
                    "host",
                    "port",
                    "lan_enabled",
                    "worker_concurrency",
                    "worker_poll_seconds",
                    "lease_seconds",
                    "busy_timeout_ms",
                )
            },
        )

    def save(self, patch: ConfigurationPatch, *, initialize=False) -> ConfigurationView:
        with Session(self.engine) as session, session.begin():
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            record = session.get(ConfigurationRecord, 1)
            revision = record.revision if record else 0
            if revision != patch.revision:
                raise ConfigurationConflict("Configuration revision changed; reload before saving")
            raw = dict(record.values_json) if record else {}
            for key in patch.reset_fields:
                if key not in ConfigurationValues.model_fields:
                    raise ConfigurationError("Unknown configuration field")
                raw.pop(key, None)
            updates = patch.values.model_dump(exclude_unset=True, mode="json")
            if "providers" in updates:
                providers = deepcopy(raw.get("providers", {}))
                for provider, fields in updates["providers"].items():
                    providers[provider] = {**providers.get(provider, {}), **fields}
                updates["providers"] = providers
            raw.update(updates)
            try:
                validate_values(ConfigurationValues.model_validate(raw))
            except ValidationError as exc:
                raise ConfigurationError(
                    "Invalid configuration value",
                    fields=[".".join(str(part) for part in error["loc"]) for error in exc.errors()],
                ) from exc
            for name, value in patch.credentials.items():
                if name not in credential_owners():
                    raise ConfigurationError("Unknown credential field")
                existing = session.get(CredentialRecord, name)
                if value is None or not value.get_secret_value():
                    if existing:
                        session.delete(existing)
                elif existing:
                    existing.value = value.get_secret_value()
                else:
                    session.add(CredentialRecord(name=name, value=value.get_secret_value()))
            if record is None:
                record = ConfigurationRecord(id=1, initialized=False)
                session.add(record)
            record.values_json = raw
            record.initialized = record.initialized or initialize
            record.revision = revision + 1
            record.updated_at = datetime.now(UTC)
            session.flush()
            return self._view(session)

    def reveal(self, name: str) -> str | None:
        if name not in credential_owners():
            raise ConfigurationError("Unknown credential field")
        with Session(self.engine) as session:
            record = session.get(CredentialRecord, name)
            return record.value if record else None

    def credentials(self) -> dict[str, str]:
        with Session(self.engine) as session:
            return {row.name: row.value for row in session.scalars(select(CredentialRecord))}

    def execution_credentials(self, run: RunSettings | None = None) -> dict[str, str]:
        with Session(self.engine) as session:
            # Explicit read transaction keeps the connection and keys at one revision.
            session.connection().exec_driver_sql("BEGIN")
            view = self._view(session)
            if not view.initialized:
                raise ConfigurationRequired(
                    "Complete configuration in Settings before starting research"
                )
            if run is not None:
                current = effective_connection(view.values, run.llm_provider)
                retained = run.connection
                if retained is None:
                    retained = effective_connection(ConfigurationValues(), run.llm_provider)
                    if run.backend_url:
                        retained["base_url"] = run.backend_url.rstrip("/")
                    if not retained.get("base_url") and run.llm_provider != "bedrock":
                        raise ProviderConfigurationChanged(
                            "Legacy connection cannot be resolved; create a new Run"
                        )
                if current != retained:
                    raise ProviderConfigurationChanged(
                        "Provider connection changed; create a new Run with current settings"
                    )
            return {row.name: row.value for row in session.scalars(select(CredentialRecord))}

    def discovery_snapshot(self):
        with Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN")
            view = self._view(session)
            secrets = {row.name: row.value for row in session.scalars(select(CredentialRecord))}
            from tradingagents.llm_clients.provider_registry import PROVIDER_REGISTRY

            connections = {
                provider: effective_connection(view.values, provider)
                for provider in PROVIDER_REGISTRY
            }
            bedrock = connections["bedrock"]
            secrets.update(
                {"AWS_REGION": bedrock["region"], "BEDROCK_AUTH_MODE": bedrock["auth_mode"]}
            )
            if bedrock["aws_profile"]:
                secrets["AWS_PROFILE"] = bedrock["aws_profile"]
            defaults = view.values
            app = self.settings.model_copy(
                update={
                    "default_run_settings": RunSettings(
                        profile=defaults.profile,
                        llm_provider=defaults.llm_provider,
                        quick_model=defaults.quick_think_llm,
                        deep_model=defaults.deep_think_llm,
                        output_language=defaults.output_language,
                        backend_url=connections[defaults.llm_provider]["base_url"],
                        data_config=defaults.model_dump(exclude={"providers"}),
                    )
                }
            )
            return app, secrets, connections, view.revision

    def resolve_request(self, request: AnalysisRequest, *, require_initialized=True):
        view = self.read()
        if require_initialized and not view.initialized:
            raise ConfigurationRequired(
                "Complete configuration in Settings before starting research"
            )
        values = view.values
        payload = request.model_dump(mode="python", exclude_unset=True)
        for field in ("profile", "analysts"):
            if field not in payload:
                payload[field] = getattr(values, field)
        aliases = {"quick_model": "quick_think_llm", "deep_model": "deep_think_llm"}
        for field in (
            "llm_provider",
            "quick_model",
            "deep_model",
            "quick_reasoning_effort",
            "deep_reasoning_effort",
            "output_language",
        ):
            if payload.get(field) is None:
                payload[field] = getattr(values, aliases.get(field, field))
        materialized = AnalysisRequest.model_validate(payload)
        data = values.model_dump(
            exclude={"providers", "profile", "analysts", "trash_retention_days"}
        )
        from tradingagents.default_config import build_default_config

        data = {**build_default_config(), **data}
        connection = effective_connection(values, materialized.llm_provider)
        resolved = RunSettings(
            profile=materialized.profile,
            llm_provider=materialized.llm_provider,
            quick_model=materialized.quick_model,
            deep_model=materialized.deep_model,
            backend_url=connection.get("base_url"),
            connection=connection,
            quick_reasoning_effort=materialized.quick_reasoning_effort,
            deep_reasoning_effort=materialized.deep_reasoning_effort,
            temperature=values.temperature,
            llm_max_retries=values.llm_max_retries,
            output_language=materialized.output_language,
            data_config=data,
        )
        return materialized, resolved

    def default_run_settings(self):
        from datetime import date

        return self.resolve_request(
            AnalysisRequest(ticker="GOOG", analysis_date=date(2000, 1, 1)),
            require_initialized=False,
        )[1]

    def preview_import(self, request: ImportRequest) -> ImportPreview:
        patch, issues, fingerprint = self._import(request)
        view = self.read()
        updates = patch.values.model_dump(mode="json", exclude_unset=True)
        return ImportPreview(
            revision=view.revision,
            fingerprint=fingerprint,
            values=updates,
            credentials=dict.fromkeys(patch.credentials, True),
            issues=issues,
            conflicts=[key for key in updates if view.sources[key] == "database"]
            + [key for key in patch.credentials if view.credentials[key]],
        )

    def apply_import(self, request: ImportRequest) -> ConfigurationView:
        if request.use_defaults:
            return self.save(ConfigurationPatch(revision=request.revision), initialize=True)
        patch, issues, fingerprint = self._import(request)
        if issues:
            raise ConfigurationError(
                "Correct or exclude the reported import fields before applying"
            )
        if not request.fingerprint or fingerprint != request.fingerprint:
            raise ConfigurationConflict("Import source changed; preview again")
        return self.save(patch, initialize=True)

    def _import(self, request: ImportRequest):
        from tradingagents.default_config import _ENV_OVERRIDES

        environment = {}
        uploaded_names = set()
        for content, original in (
            (request.enterprise, self.settings.import_enterprise),
            (request.primary, self.settings.import_primary),
        ):
            parsed = (
                dotenv_values(stream=StringIO(content.get_secret_value()), interpolate=False)
                if content is not None
                else {k: v.get_secret_value() for k, v in original.items()}
            )
            environment.update({k: v for k, v in parsed.items() if v is not None})
            uploaded_names.update(parsed)
        environment.update(
            {k: v.get_secret_value() for k, v in self.settings.import_environment.items()}
        )
        environment = {k: v for k, v in environment.items() if k not in request.exclude and v != ""}
        values, secrets, issues = {}, {}, []
        provider = environment.get("TRADINGAGENTS_LLM_PROVIDER", "openai")
        providers = {}
        connection_fields = {
            "OLLAMA_BASE_URL": ("ollama", "base_url"),
            "AZURE_OPENAI_ENDPOINT": ("azure", "base_url"),
            "AZURE_OPENAI_DEPLOYMENT_NAME": ("azure", "deployment"),
            "OPENAI_API_VERSION": ("azure", "api_version"),
            "AWS_DEFAULT_REGION": ("bedrock", "region"),
            "AWS_REGION": ("bedrock", "region"),
            "AWS_PROFILE": ("bedrock", "aws_profile"),
            "TRADINGAGENTS_LLM_BACKEND_URL": (provider, "base_url"),
        }
        mapping = {**_ENV_OVERRIDES, "TRADINGAGENTS_TRASH_RETENTION_DAYS": "trash_retention_days"}
        owners = credential_owners()
        for name, value in environment.items():
            if name in owners:
                secrets[name] = SecretStr(value)
            elif name in mapping and name not in connection_fields:
                field = mapping[name]
                try:
                    checked = ConfigurationValues.model_validate({field: value})
                    parsed = getattr(checked, field)
                    if field == "output_language":
                        parsed = report_language_value(parsed)
                    values[field] = parsed
                except (ValidationError, ValueError):
                    issues.append(
                        ImportIssue(
                            name=name, message="Invalid value or outside the supported range"
                        )
                    )
            elif (
                (name in uploaded_names or name.startswith("TRADINGAGENTS_"))
                and name not in connection_fields
                and name not in BOOTSTRAP_ENV
            ):
                issues.append(ImportIssue(name=name, message="Unsupported import field"))
        for name, (owner, field) in connection_fields.items():
            if name in environment:
                providers.setdefault(owner, {})[field] = environment[name]
        if "AWS_BEARER_TOKEN_BEDROCK" in secrets:
            providers.setdefault("bedrock", {})["auth_mode"] = "bearer"
        elif "AWS_ACCESS_KEY_ID" in secrets or "AWS_SECRET_ACCESS_KEY" in secrets:
            providers.setdefault("bedrock", {})["auth_mode"] = "static"
        valid_providers = {}
        for name, connection in providers.items():
            try:
                valid_providers[name] = ProviderConnection.model_validate(connection).model_dump()
            except ValidationError as exc:
                invalid_fields = {error["loc"][0] for error in exc.errors()}
                issues.extend(
                    ImportIssue(name=source, message="Invalid provider connection")
                    for source, (owner, field) in connection_fields.items()
                    if source in environment and owner == name and field in invalid_fields
                )
        if valid_providers:
            values["providers"] = valid_providers
        try:
            validate_values(ConfigurationValues.model_validate(values))
        except ValueError:
            from tradingagents.llm_clients.provider_registry import PROVIDER_REGISTRY

            invalid_names = (
                ["TRADINGAGENTS_LLM_PROVIDER"]
                if provider not in PROVIDER_REGISTRY
                else [
                    name
                    for name in environment
                    if "REASONING" in name or "THINKING_LEVEL" in name or "ANTHROPIC_EFFORT" in name
                ]
            )
            issues.extend(
                ImportIssue(name=name, message="Provider or reasoning setting is invalid")
                for name in invalid_names
            )
        fingerprint = sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest()
        return (
            ConfigurationPatch(revision=request.revision, values=values, credentials=secrets),
            issues,
            fingerprint,
        )


BOOTSTRAP_ENV = {
    "TRADINGAGENTS_HOME",
    "TRADINGAGENTS_DATABASE_PATH",
    "TRADINGAGENTS_CACHE_DIR",
    "TRADINGAGENTS_HOST",
    "TRADINGAGENTS_PORT",
    "TRADINGAGENTS_WORKER_POLL_SECONDS",
    "TRADINGAGENTS_LEASE_SECONDS",
    "TRADINGAGENTS_SQLITE_BUSY_TIMEOUT_MS",
    "TRADINGAGENTS_PUBLISH_HOST",
    "TRADINGAGENTS_WEB_PORT",
    "TRADINGAGENTS_ENV_FILE",
    "TRADINGAGENTS_LAN_ENABLED",
    "TRADINGAGENTS_LAN_TOKEN",
    "TRADINGAGENTS_SESSION_SECRET",
}


def effective_connection(values, provider):
    from tradingagents.llm_clients.provider_registry import PROVIDER_REGISTRY

    if provider not in PROVIDER_REGISTRY:
        raise ConfigurationError("Unknown model provider")
    connection = deepcopy(values.providers.get(provider, ProviderConnection()).model_dump())
    connection["base_url"] = connection["base_url"] or PROVIDER_REGISTRY[provider].default_base_url
    if connection["base_url"]:
        connection["base_url"] = connection["base_url"].rstrip("/")
    return connection


def validate_values(values):
    from tradingagents.dataflows.interface import validate_market_routing
    from tradingagents.llm_clients.provider_registry import PROVIDER_REGISTRY
    from tradingagents.llm_clients.reasoning_effort import resolve_reasoning_effort

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
    from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV

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


def configuration_credentials(function):
    """Bind one credential snapshot around admission or retry data queries."""

    @wraps(function)
    def wrapped(self, *args, **kwargs):
        from tradingagents.credentials import use_credentials

        with use_credentials(self.configuration.execution_credentials()):
            return function(self, *args, **kwargs)

    return wrapped
