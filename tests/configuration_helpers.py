"""Explicit configuration setup for tests that exercise configured applications."""

from tradingagents.application.configuration import ConfigurationStore, credential_owners
from tradingagents.application.configuration_models import ConfigurationPatch
from tradingagents.persistence import upgrade_database


def initialize_configuration(settings):
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    if not store.read().initialized:
        store.save(
            ConfigurationPatch(
                revision=0, credentials=dict.fromkeys(credential_owners(), "placeholder")
            ),
            initialize=True,
        )
    return settings


def import_configuration(settings):
    from tradingagents.application.configuration_models import ImportRequest

    upgrade_database(settings)
    store = ConfigurationStore(settings)
    preview = store.preview_import(ImportRequest())
    store.apply_import(ImportRequest(revision=preview.revision, fingerprint=preview.fingerprint))
    return store


def save_configuration(settings, values=None, credentials=None):
    initialize_configuration(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(
            revision=store.read().revision, values=values or {}, credentials=credentials or {}
        )
    )
    return store
