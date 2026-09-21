"""Connection persistence inside the configuration and Run transactions."""

from sqlalchemy import select

from tradingagents.llm.models import (
    ModelConnection,
    connection_view,
    credential_name,
    preset_connection,
)
from tradingagents.persistence.models import CredentialRecord, ModelConnectionRecord, RunRecord


def connection_records(session):
    return {
        row.id: ModelConnection.model_validate(row.definition)
        for row in session.scalars(select(ModelConnectionRecord))
    }


def connection_secrets(session, connection):
    return {
        field: row.value
        for field in connection.credential_fields()
        if (row := session.get(CredentialRecord, credential_name(connection.id, field))) is not None
    }


def views(session):
    return {
        identity: connection_view(conn, connection_secrets(session, conn))
        for identity, conn in connection_records(session).items()
        if not conn.deleted
    }


def referenced_ids(snapshot):
    ids = {
        binding["connection"]["id"]
        for role in (("deep",) if snapshot.get("research_kind") == "incremental" else ("quick", "deep"))
        if (binding := snapshot.get(f"{role}_binding"))
    }
    return ids


def apply_changes(session, changes, raw):
    from tradingagents.configuration.errors import ConfigurationError
    from tradingagents.llm.provider_registry import PROVIDER_REGISTRY

    for change in changes:
        row = session.get(ModelConnectionRecord, change.id)
        if change.action == "create":
            if row is not None:
                raise ConfigurationError("Connection ID already exists", fields=["connections"])
            base = preset_connection(
                change.preset or "openai_compatible", identity=change.id, name=change.name
            )
            row = ModelConnectionRecord(id=change.id, definition=base.model_dump(mode="json"))
            session.add(row)
        elif row is None or row.definition.get("deleted"):
            raise ConfigurationError(
                "Connection was deleted or does not exist", fields=["connections"]
            )
        conn = ModelConnection.model_validate(row.definition)
        if change.action == "delete":
            if change.id in {(raw.get("models", {}).get(role) or {}).get("connection_id") for role in ("quick", "deep")}:
                raise ConfigurationError(
                    "Replace the default connection before deleting it", fields=["connections"]
                )
            active = list(
                session.scalars(
                    select(RunRecord).where(RunRecord.status.in_(("queued", "running")))
                )
            )
            blockers = [run.id for run in active if change.id in referenced_ids(run.config_json)]
            if blockers:
                raise ConfigurationError(
                    "Connection is used by unfinished Runs: " + ", ".join(blockers[:5]),
                    fields=["connections"],
                )
            conn = conn.model_copy(update={"deleted": True, "enabled": False})
            for secret in session.scalars(select(CredentialRecord)):
                if secret.name.startswith(f"connection:{conn.id}:"):
                    session.delete(secret)
        else:
            payload = conn.model_dump(mode="json")
            if change.action == "reset":
                payload.update(conn.template)
            else:
                payload.update(
                    change.model_dump(
                        exclude_unset=True,
                        exclude={"action", "id", "preset", "credentials"},
                        mode="json",
                    )
                )
            conn = ModelConnection.model_validate(payload)
            if conn.compatibility not in PROVIDER_REGISTRY:
                raise ConfigurationError("Unknown compatibility policy", fields=["connections"])
            native = {"anthropic", "google", "azure", "bedrock"}
            if (conn.transport.kind in native and conn.compatibility != conn.transport.kind) or (
                conn.transport.kind not in native and conn.compatibility in native
            ):
                raise ConfigurationError(
                    "Compatibility policy does not match the interface", fields=["connections"]
                )
            allowed_discovery = (
                {"openai_compatible", "ollama", "custom"}
                if conn.transport.kind not in native
                else {conn.transport.kind, "custom"}
            )
            if conn.transport.kind == "azure":
                allowed_discovery = {"custom"}
            if conn.discovery not in allowed_discovery:
                raise ConfigurationError(
                    "Model discovery strategy does not match the interface", fields=["connections"]
                )
            if conn.transport.kind in {"anthropic", "google", "azure"} and not conn.key_required:
                raise ConfigurationError(
                    "This interface requires an API key", fields=["connections"]
                )
            if change.action == "create":
                conn = conn.model_copy(
                    update={"template": {k: conn.model_dump()[k] for k in conn.template}}
                )
            for field, value in change.credentials.items():
                if field not in conn.credential_fields():
                    raise ConfigurationError(
                        "Unknown connection credential", fields=["connections"]
                    )
                name = credential_name(conn.id, field)
                secret = session.get(CredentialRecord, name)
                if value is None or not value.get_secret_value():
                    if secret:
                        session.delete(secret)
                elif secret:
                    secret.value = value.get_secret_value()
                else:
                    session.add(CredentialRecord(name=name, value=value.get_secret_value()))
        old = ModelConnection.model_validate(row.definition)
        changed = (
            old.execution_identity() != conn.execution_identity()
            or old.discovery != conn.discovery
            or bool(change.credentials)
        )
        row.definition = conn.model_copy(
            update={"revision": old.revision + int(changed)}
        ).model_dump(mode="json")
        session.flush()
    for role in ("quick", "deep"):
        row = session.get(ModelConnectionRecord, (raw.get("models", {}).get(role) or {}).get("connection_id"))
        if row is None or row.definition.get("deleted") or not row.definition.get("enabled", True):
            raise ConfigurationError(
                "Research defaults must select an enabled connection",
                fields=[f"models.{role}.connection_id"],
            )
        from tradingagents.configuration.models import ConfigurationValues
        from tradingagents.llm.reasoning_effort import resolve_reasoning_effort

        values = ConfigurationValues.model_validate(raw)
        conn = ModelConnection.model_validate(row.definition)
        try:
            selection = getattr(values.models, role)
            resolve_reasoning_effort(
                conn.compatibility, selection.model, selection.reasoning_effort,
                connection_default=conn.reasoning_effort,
            )
        except ValueError as exc:
            raise ConfigurationError(
                "Unsupported reasoning setting for this connection",
                fields=[f"models.{role}.reasoning_effort"],
            ) from exc


def validate_run_connections(session, snapshot, *, retry=False):
    """Called under the Run admission write lock, closing deletion/admission races."""
    from tradingagents.configuration.errors import ProviderConfigurationChanged

    for identity in referenced_ids(snapshot):
        row = session.get(ModelConnectionRecord, identity)
        if row is None:
            raise ProviderConfigurationChanged("Connection does not exist; create a new Run")
        current = ModelConnection.model_validate(row.definition)
        if current.deleted or (not retry and not current.enabled):
            raise ProviderConfigurationChanged("Connection unavailable; select another connection")
        for role in (("deep",) if snapshot.get("research_kind") == "incremental" else ("quick", "deep")):
            binding = snapshot.get(f"{role}_binding")
            if binding and binding["connection"]["id"] == identity:
                retained = ModelConnection.model_validate(binding["connection"])
                if retained.execution_identity() != current.execution_identity():
                    raise ProviderConfigurationChanged("Connection changed; create a new Run")
