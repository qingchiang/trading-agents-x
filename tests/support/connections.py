"""Shared offline fixtures for connections contracts."""

from tradingagents.configuration.models import ConfigurationPatch
from tradingagents.configuration.settings import AppSettings
from tradingagents.persistence.configuration import ConfigurationStore
from tradingagents.persistence.migrations import upgrade_database


def configured_store(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(
            revision=0,
            connection_changes=[
                {
                    "action": "create",
                    "id": "primary",
                    "name": "Primary",
                    "preset": "openai_compatible",
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://primary.example/v1",
                    },
                    "credentials": {"api_key": "primary-secret"},
                },
                {
                    "action": "create",
                    "id": "secondary",
                    "name": "Secondary",
                    "preset": "openai_compatible",
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://secondary.example/v1",
                    },
                    "credentials": {"api_key": "secondary-secret"},
                },
            ],
            values={
                "models": {
                    "quick": {"connection_id": "primary"},
                    "deep": {"connection_id": "secondary"},
                }
            },
        ),
        initialize=True,
    )
    return settings, store
