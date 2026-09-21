"""Configuration."""

import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO

from dotenv import dotenv_values
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradingagents.configuration.errors import (
    ConfigurationConflict,
    ConfigurationError,
    ConfigurationRequired,
    ProviderConfigurationChanged,
)
from tradingagents.configuration.models import (
    ConfigurationPatch,
    ConfigurationValues,
    ConfigurationView,
    ImportIssue,
    ImportPreview,
    ImportRequest,
    ProviderConnection,
)
from tradingagents.configuration.resolution import credential_owners, validate_values
from tradingagents.configuration.settings import AppSettings, RunSettings
from tradingagents.domain.common import report_language_value
from tradingagents.domain.runs import AnalysisRequest
from tradingagents.llm.models import (
    ModelBinding,
    ModelConnection,
    connection_view,
    credential_name,
    legacy_connection_id,
    legacy_credential_fields,
    preset_connection,
)
from tradingagents.persistence import connections as connections
from tradingagents.persistence.models import (
    ConfigurationRecord,
    CredentialRecord,
    ModelConnectionRecord,
    create_sqlite_engine,
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
            models = {}
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='model_connections'"
            ).fetchone():
                for (retained,) in connection.execute("SELECT definition FROM model_connections"):
                    conn = ModelConnection.model_validate_json(retained)
                    if not conn.deleted:
                        models[conn.id] = connection_view(
                            conn,
                            {
                                field: credential_name(conn.id, field) in names
                                for field in conn.credential_fields()
                            },
                        )
            return self._document(
                json.loads(row[0]) if row else {},
                names,
                row[1] if row else 0,
                bool(row[2]) if row else False,
                models,
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
            connections.views(session),
        )

    def _document(self, raw, configured, revision, initialized, models=None):
        stored_keys = set(raw)
        models = models or {}
        if (
            not raw.get("quick_connection_id")
            and legacy_connection_id(raw.get("llm_provider", "openai")) in models
        ):
            raw = {
                **raw,
                "quick_connection_id": legacy_connection_id(raw.get("llm_provider", "openai")),
                "deep_connection_id": legacy_connection_id(raw.get("llm_provider", "openai")),
            }
        for alias, (owner, field) in legacy_credential_fields().items():
            if credential_name(legacy_connection_id(owner), field) in configured:
                configured.add(alias)
        return ConfigurationView(
            connections=models,
            initialized=initialized,
            revision=revision,
            values=ConfigurationValues.model_validate(raw),
            sources={
                key: "database" if key in stored_keys else "default"
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
            if set(patch.credentials) - set(credential_owners()):
                raise ConfigurationError("Unknown credential field")
            try:
                translated = connections.sync_legacy(session, raw, updates, patch.credentials)
                connections.apply_changes(session, patch.connection_changes, raw)
            except ValueError as exc:
                if isinstance(exc, ConfigurationError):
                    raise
                raise ConfigurationError(
                    "Invalid connection configuration", fields=["connections"]
                ) from exc
            for name, value in translated.items():
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
        alias = legacy_credential_fields().get(name)
        if alias:
            name = credential_name(legacy_connection_id(alias[0]), alias[1])
        with Session(self.engine) as session:
            record = session.get(CredentialRecord, name)
            return record.value if record else None

    def reveal_connection(self, connection_id, field):
        with Session(self.engine) as session:
            row = session.get(ModelConnectionRecord, connection_id)
            if row is None or row.definition.get("deleted"):
                raise ConfigurationError("Connection does not exist")
            conn = ModelConnection.model_validate(row.definition)
            if field not in conn.credential_fields():
                raise ConfigurationError("Unknown credential field")
            return connections.connection_secrets(session, conn).get(field)

    def credentials(self) -> dict[str, str]:
        with Session(self.engine) as session:
            return self._credential_snapshot(session)

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
                roles = ("deep",) if run.research_kind == "incremental" else ("quick", "deep")
                for role in roles:
                    binding = getattr(run, f"{role}_binding")
                    if binding:
                        row = session.get(ModelConnectionRecord, binding.connection.id)
                        if row is None or row.definition.get("deleted"):
                            raise ProviderConfigurationChanged(
                                "Connection deleted; create a new Run"
                            )
                        current = ModelConnection.model_validate(row.definition)
                        if current.execution_identity() != binding.connection.execution_identity():
                            raise ProviderConfigurationChanged(
                                "Connection changed; create a new Run"
                            )
                        missing = current.missing_fields(
                            connections.connection_secrets(session, current)
                        )
                        if missing:
                            raise ConfigurationError("Connection requires: " + ", ".join(missing))
                    else:
                        row = session.get(
                            ModelConnectionRecord, legacy_connection_id(run.llm_provider)
                        )
                        if row is None or row.definition.get("deleted"):
                            raise ProviderConfigurationChanged(
                                "Legacy connection deleted; create a new Run"
                            )
                        current = ModelConnection.model_validate(row.definition)
                        retained = preset_connection(
                            run.llm_provider, legacy=run.connection or {"base_url": run.backend_url}
                        )
                        if current.execution_identity() != retained.execution_identity():
                            raise ProviderConfigurationChanged(
                                "Provider connection changed; create a new Run"
                            )
            return self._credential_snapshot(session)

    def _credential_snapshot(self, session):
        values = {row.name: row.value for row in session.scalars(select(CredentialRecord))}
        # Compatibility clients resolve only the immutable migrated identity.
        for alias, (owner, field) in legacy_credential_fields().items():
            value = values.get(credential_name(legacy_connection_id(owner), field))
            if value:
                values[alias] = value
        return values

    def connection_discovery_snapshot(self, identity):
        with Session(self.engine) as session:
            session.connection().exec_driver_sql("BEGIN")
            row = session.get(ModelConnectionRecord, identity)
            if row is None or row.definition.get("deleted"):
                raise ConfigurationError("Connection does not exist")
            conn = ModelConnection.model_validate(row.definition)
            return conn, connections.connection_secrets(session, conn), self._view(session).values

    def resolve_request(self, request: AnalysisRequest, *, require_initialized=True):
        view = self.read()
        if require_initialized and not view.initialized:
            raise ConfigurationRequired(
                "Complete configuration in Settings before starting research"
            )
        values = view.values
        explicit_connections = any(
            getattr(request, field) is not None
            for field in (
                ("connection_id", "deep_connection_id")
                if request.research_kind == "incremental"
                else ("connection_id", "quick_connection_id", "deep_connection_id")
            )
        )
        if request.llm_provider is not None and explicit_connections:
            raise ConfigurationError(
                "Use connection IDs or legacy provider, not both", fields=["llm_provider"]
            )
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
        bindings = {}
        for role in (("deep",) if request.research_kind == "incremental" else ("quick", "deep")):
            identity = (
                getattr(request, f"{role}_connection_id")
                or request.connection_id
                or (legacy_connection_id(request.llm_provider) if request.llm_provider else None)
                or getattr(values, f"{role}_connection_id")
                or legacy_connection_id(values.llm_provider)
            )
            entry = view.connections.get(identity)
            if entry is None:
                if not require_initialized:
                    conn = preset_connection(values.llm_provider)
                else:
                    raise ConfigurationError(
                        "Connection does not exist", fields=[f"{role}_connection_id"]
                    )
            else:
                conn = entry.connection
                if require_initialized and not conn.enabled:
                    raise ConfigurationError(
                        "Connection is disabled", fields=[f"{role}_connection_id"]
                    )
            bindings[role] = ModelBinding(
                connection=conn,
                model=payload[f"{role}_model"],
                reasoning_effort=payload[f"{role}_reasoning_effort"],
            )
            from tradingagents.llm.reasoning_effort import resolve_reasoning_effort

            try:
                resolve_reasoning_effort(
                    {
                        **conn.reasoning_defaults,
                        "llm_provider": conn.compatibility,
                        "quick_think_llm": bindings[role].model,
                        "quick_reasoning_effort": bindings[role].reasoning_effort,
                    },
                    "quick",
                )
            except ValueError as exc:
                raise ConfigurationError(
                    "Unsupported reasoning setting for this role",
                    fields=[f"{role}_reasoning_effort"],
                ) from exc
            payload[f"{role}_connection_id"] = conn.id
        if request.research_kind == "incremental":
            bindings["quick"] = bindings["deep"]
            for field in ("connection_id", "model", "reasoning_effort"):
                payload[f"quick_{field}"] = payload[f"deep_{field}"]
        # Materialized requests carry identities; legacy input is consumed above.
        payload["llm_provider"] = None
        materialized = AnalysisRequest.model_validate(payload)
        data = values.model_dump(
            exclude={"providers", "profile", "analysts", "trash_retention_days"}
        )
        from tradingagents.configuration.defaults import build_default_config

        data = {**build_default_config(), **data}
        connection = bindings["deep"].connection.transport.model_dump(exclude={"kind"})
        resolved = RunSettings(
            profile=materialized.profile,
            llm_provider=(
                bindings["deep"].connection.compatibility
                if bindings["quick"].connection.id == bindings["deep"].connection.id
                else ""
            ),
            quick_binding=bindings["quick"],
            deep_binding=bindings["deep"],
            research_kind=materialized.research_kind,
            quick_model=materialized.quick_model,
            deep_model=materialized.deep_model,
            backend_url=connection.get("base_url")
            if bindings["quick"].connection.id == bindings["deep"].connection.id
            else None,
            connection=connection
            if bindings["quick"].connection.id == bindings["deep"].connection.id
            else None,
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
        targets = {}
        for name in patch.credentials:
            alias = legacy_credential_fields().get(name)
            if alias:
                targets[name] = f"{legacy_connection_id(alias[0])}.{alias[1]}"
        for owner in updates.get("providers", {}):
            targets[f"providers.{owner}"] = legacy_connection_id(owner)
        if "llm_provider" in updates:
            targets["llm_provider"] = legacy_connection_id(updates["llm_provider"])
        if self.settings.database_path.exists():
            with closing(
                sqlite3.connect(self.settings.database_path.as_uri() + "?mode=ro", uri=True)
            ) as connection:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='model_connections'"
                ).fetchone():
                    deleted = {
                        row[0]
                        for row in connection.execute(
                            "SELECT id FROM model_connections WHERE json_extract(definition, '$.deleted')=1"
                        )
                    }
                    for name, target in targets.items():
                        if target.split(".")[0] in deleted:
                            issues.append(
                                ImportIssue(
                                    name=name,
                                    message="Original connection was deleted; exclude this field and configure a new connection manually",
                                )
                            )
        return ImportPreview(
            connection_targets=targets,
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
        issues = self.preview_import(request).issues
        if issues:
            raise ConfigurationError(
                "Correct or exclude the reported import fields before applying"
            )
        if not request.fingerprint or fingerprint != request.fingerprint:
            raise ConfigurationConflict("Import source changed; preview again")
        return self.save(patch, initialize=True)

    def _import(self, request: ImportRequest):
        from tradingagents.configuration.defaults import _ENV_OVERRIDES

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
            from tradingagents.llm.provider_registry import PROVIDER_REGISTRY

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
