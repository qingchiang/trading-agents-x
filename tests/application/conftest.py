from __future__ import annotations

from pathlib import Path

import pytest

from tradingagents.application.repository import RunRepository
from tradingagents.application.settings import AppSettings
from tradingagents.persistence import upgrade_database


@pytest.fixture
def app_settings(tmp_path: Path) -> AppSettings:
    return AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_DATABASE_PATH": str(tmp_path / "tradingagents.db"),
            "TRADINGAGENTS_CACHE_DIR": str(tmp_path / "cache"),
        },
        load_env_files=False,
    )


@pytest.fixture
def repository(app_settings: AppSettings) -> RunRepository:
    upgrade_database(app_settings)
    return RunRepository(app_settings)
