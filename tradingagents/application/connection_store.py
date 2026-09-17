"""Connection persistence inside the configuration and Run transactions."""

from sqlalchemy import select

from .database import CredentialRecord, ModelConnectionRecord, RunRecord
from .model_connections import (
    ModelConnection,
    connection_view,
    credential_name,
    legacy_connection_id,
    legacy_credential_fields,
    preset_connection,
)


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


def ensure_legacy(session, provider, raw):
    identity = legacy_connection_id(provider)
    row = session.get(ModelConnectionRecord, identity)
    if row is None:
        conn = preset_connection(
            provider,
            legacy=raw.get("providers", {}).get(provider),
            defaults={
                k: raw[k]
                for k in ("openai_reasoning_effort", "google_thinking_level", "anthropic_effort")
                if k in raw
            },
        )
        row = ModelConnectionRecord(
            id=identity, legacy_provider=provider, definition=conn.model_dump(mode="json")
        )
        session.add(row)
        session.flush()
    if row.definition.get("deleted"):
        raise ValueError("Legacy connection was deleted; select a new connection explicitly")
    return row


def sync_legacy(session, raw, updates, credential_updates):
    """Translate compatibility inputs once, never infer identity from a label."""
    provider = raw.get("llm_provider", "openai")
    if (
        not raw.get("quick_connection_id")
        or not raw.get("deep_connection_id")
        or "llm_provider" in updates
    ):
        row = ensure_legacy(session, provider, raw)
        raw["quick_connection_id"] = row.id
        raw["deep_connection_id"] = row.id
    for owner in updates.get("providers", {}):
        row = ensure_legacy(session, owner, raw)
        conn = ModelConnection.model_validate(row.definition)
        translated = preset_connection(owner, legacy=raw["providers"][owner])
        row.definition = conn.model_copy(
            update={"transport": translated.transport, "revision": conn.revision + 1}
        ).model_dump(mode="json")
    for row in session.scalars(select(ModelConnectionRecord)):
        native = {
            key: value
            for key, value in updates.items()
            if key in {"openai_reasoning_effort", "google_thinking_level", "anthropic_effort"}
        }
        if native and row.legacy_provider and not row.definition.get("deleted"):
            conn = ModelConnection.model_validate(row.definition)
            row.definition = conn.model_copy(
                update={"reasoning_defaults": {**conn.reasoning_defaults, **native}}
            ).model_dump(mode="json")
    translated_credentials = {}
    aliases = legacy_credential_fields()
    for name, value in credential_updates.items():
        if name in aliases:
            owner, field = aliases[name]
            row = ensure_legacy(session, owner, raw)
            translated_credentials[credential_name(row.id, field)] = value
            definition = dict(row.definition)
            definition["revision"] += 1
            row.definition = definition
        else:
            translated_credentials[name] = value
    return translated_credentials


def referenced_ids(snapshot):
    ids = {
        binding["connection"]["id"]
        for role in ("quick", "deep")
        if (binding := snapshot.get(f"{role}_binding"))
    }
    if not ids and snapshot.get("llm_provider"):
        ids.add(legacy_connection_id(snapshot["llm_provider"]))
    return ids


def apply_changes(session, changes, raw):
    from tradingagents.llm_clients.provider_registry import PROVIDER_REGISTRY

    from .configuration import ConfigurationError

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
            if change.id in {raw.get("quick_connection_id"), raw.get("deep_connection_id")}:
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
        row = session.get(ModelConnectionRecord, raw.get(f"{role}_connection_id"))
        if row is None or row.definition.get("deleted") or not row.definition.get("enabled", True):
            raise ConfigurationError(
                "Research defaults must select an enabled connection",
                fields=[f"{role}_connection_id"],
            )
        from tradingagents.llm_clients.reasoning_effort import resolve_reasoning_effort

        from .configuration_models import ConfigurationValues

        values = ConfigurationValues.model_validate(raw)
        conn = ModelConnection.model_validate(row.definition)
        try:
            resolve_reasoning_effort(
                {
                    **conn.reasoning_defaults,
                    "llm_provider": conn.compatibility,
                    "quick_think_llm": getattr(values, f"{role}_think_llm"),
                    "quick_reasoning_effort": getattr(values, f"{role}_reasoning_effort"),
                },
                "quick",
            )
        except ValueError as exc:
            raise ConfigurationError(
                "Unsupported reasoning setting for this connection",
                fields=[f"{role}_reasoning_effort"],
            ) from exc


def validate_run_connections(session, snapshot, *, retry=False):
    """Called under the Run admission write lock, closing deletion/admission races."""
    from .configuration import ProviderConfigurationChanged

    for identity in referenced_ids(snapshot):
        row = session.get(ModelConnectionRecord, identity)
        if row is None:
            if not snapshot.get("quick_binding"):
                continue
            raise ProviderConfigurationChanged("Connection does not exist; create a new Run")
        current = ModelConnection.model_validate(row.definition)
        if current.deleted or (not retry and not current.enabled):
            raise ProviderConfigurationChanged("Connection unavailable; select another connection")
        for role in ("quick", "deep"):
            binding = snapshot.get(f"{role}_binding")
            if binding and binding["connection"]["id"] == identity:
                retained = ModelConnection.model_validate(binding["connection"])
                if retained.execution_identity() != current.execution_identity():
                    raise ProviderConfigurationChanged("Connection changed; create a new Run")
