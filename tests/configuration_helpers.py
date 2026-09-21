"""Explicit configuration setup for tests that exercise configured applications."""

from tradingagents.configuration.models import ConfigurationPatch
from tradingagents.configuration.resolution import credential_owners
from tradingagents.persistence import upgrade_database
from tradingagents.persistence.configuration import ConfigurationStore


def initialize_configuration(settings):
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    if not store.read().initialized:
        store.save(
            ConfigurationPatch(
                revision=0, credentials=dict.fromkeys(credential_owners(), "placeholder"),
                connection_changes=[{"action": "create", "id": "default", "preset": "openai", "credentials": {"api_key": "placeholder"}}]
            ),
            initialize=True,
        )
    return settings


def import_configuration(settings):
    from tradingagents.configuration.models import ImportRequest

    upgrade_database(settings)
    store = ConfigurationStore(settings)
    preview = store.preview_import(ImportRequest())
    store.apply_import(ImportRequest(revision=preview.revision, fingerprint=preview.fingerprint))
    return store


def save_configuration(settings, values=None, credentials=None, connection_changes=None):
    initialize_configuration(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(
            revision=store.read().revision, values=values or {}, credentials=credentials or {},
            connection_changes=connection_changes or []
        )
    )
    return store
