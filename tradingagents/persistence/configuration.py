"""Configuration."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from pydantic import ValidationError
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
    ImportPreview,
    ImportRequest,
)
from tradingagents.configuration.resolution import credential_owners, validate_values
from tradingagents.configuration.settings import AppSettings, RunSettings
from tradingagents.domain.model_selection import ModelSelection, RoleSelections
from tradingagents.domain.runs import AnalysisRequest
from tradingagents.llm.models import (
    ModelBinding,
    ModelConnection,
    connection_view,
    credential_name,
    preset_connection,
)
from tradingagents.persistence import connections as connections
from tradingagents.persistence.models import (
    ConfigurationRecord,
    CredentialRecord,
    ModelConnectionRecord,
    create_sqlite_engine,
)


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
            if "models" in updates:
                defaults = ConfigurationValues().models.model_dump()
                selected = {**defaults, **raw.get("models", {})}
                for role, fields in updates["models"].items():
                    selected[role] = {**(selected.get(role) or {}), **(fields or {})}
                updates["models"] = selected
            raw.update(updates)
            if not raw.get("models"):
                raw["models"] = ConfigurationValues().models.model_dump()
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
                if record is None and session.get(ModelConnectionRecord, "default") is None and not any(change.id == "default" and change.action == "create" for change in patch.connection_changes):
                    conn = preset_connection("openai", identity="default")
                    session.add(ModelConnectionRecord(id=conn.id, definition=conn.model_dump(mode="json")))
                    session.flush()
                connections.apply_changes(session, patch.connection_changes, raw)
            except ValueError as exc:
                if isinstance(exc, ConfigurationError):
                    raise
                raise ConfigurationError(
                    "Invalid connection configuration", fields=["connections"]
                ) from exc
            for name, value in patch.credentials.items():
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
            return self._credential_snapshot(session)

    def _credential_snapshot(self, session):
        values = {row.name: row.value for row in session.scalars(select(CredentialRecord))}
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
        request = AnalysisRequest.model_validate(request.model_dump(mode="python", exclude_unset=True))
        view = self.read()
        if require_initialized and not view.initialized:
            raise ConfigurationRequired("Complete configuration in Settings before starting research")
        values = view.values
        payload = request.model_dump(mode="python", exclude_unset=True)
        for field in ("profile", "analysts", "output_language"):
            if payload.get(field) is None:
                payload[field] = getattr(values, field)
        selections, bindings = {}, {}
        for role in (("deep",) if request.research_kind == "incremental" else ("quick", "deep")):
            selection = (getattr(request.models, role) or ModelSelection()).inherit(
                getattr(values.models, role) or ModelSelection()
            )
            entry = view.connections.get(selection.connection_id)
            if entry is None:
                if not require_initialized and selection.connection_id == "default":
                    connection = preset_connection("openai", identity="default")
                else:
                    raise ConfigurationError("Connection does not exist", fields=[f"models.{role}.connection_id"])
            else:
                connection = entry.connection
                if not connection.enabled:
                    raise ConfigurationError("Connection is disabled", fields=[f"models.{role}.connection_id"])
            if not selection.model:
                raise ConfigurationError("Select a model", fields=[f"models.{role}.model"])
            binding = ModelBinding(connection=connection, model=selection.model, reasoning_effort=selection.reasoning_effort)
            from tradingagents.llm.connections import validate_binding
            try:
                validate_binding(binding)
            except ValueError as exc:
                raise ConfigurationError(str(exc), fields=[f"models.{role}.reasoning_effort"]) from None
            selections[role], bindings[role] = selection, binding
        payload["models"] = RoleSelections(**selections)
        materialized = AnalysisRequest.model_validate(payload)
        from tradingagents.configuration.defaults import build_default_config
        data = values.model_dump(exclude={"models", "profile", "analysts", "trash_retention_days"})
        data = {**build_default_config(), **data}
        return materialized, RunSettings(
            profile=materialized.profile,
            quick_binding=bindings.get("quick"), deep_binding=bindings["deep"],
            research_kind=materialized.research_kind, temperature=values.temperature,
            llm_max_retries=values.llm_max_retries, output_language=materialized.output_language,
            data_config=data,
        )

    def default_run_settings(self):
        from datetime import date

        return self.resolve_request(
            AnalysisRequest(ticker="GOOG", analysis_date=date(2000, 1, 1)),
            require_initialized=False,
        )[1]

    def preview_import(self, request: ImportRequest) -> ImportPreview:
        imported = self._import(request)
        view = self.read()
        updates = imported.patch.values.model_dump(mode="json", exclude_unset=True)
        return ImportPreview(
            connection_targets=imported.targets, revision=view.revision,
            fingerprint=imported.fingerprint, values=updates,
            credentials=imported.credentials, issues=imported.issues,
            conflicts=[key for key in updates if view.sources.get(key) == "database"],
        )

    def apply_import(self, request: ImportRequest) -> ConfigurationView:
        if request.use_defaults:
            return self.save(ConfigurationPatch(revision=request.revision), initialize=True)
        imported = self._import(request)
        if imported.issues:
            raise ConfigurationError("Correct or exclude the reported import fields before applying")
        if not request.fingerprint or request.fingerprint != imported.fingerprint:
            raise ConfigurationConflict("Import source changed; preview again")
        return self.save(imported.patch, initialize=True)

    def _import(self, request):
        from tradingagents.configuration.importing import parse_import
        existing = {identity: view.connection for identity, view in self.read().connections.items()}
        if self.settings.database_path.exists():
            with closing(sqlite3.connect(self.settings.database_path.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='model_connections'").fetchone():
                    existing = {identity: ModelConnection.model_validate_json(definition)
                                for identity, definition in db.execute("SELECT id, definition FROM model_connections")}
        return parse_import(self.settings, request, existing)
