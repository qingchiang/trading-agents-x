from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx2 as httpx
import pytest

from tradingagents.application.repository import RunRepository
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.persistence import upgrade_database
from tradingagents.web import create_app


def _equity_resolver(ticker: str) -> dict[str, str]:
    return {"symbol": ticker, "quote_type": "EQUITY"}


@pytest.fixture
def web_settings(tmp_path: Path) -> AppSettings:
    return AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_DATABASE_PATH": str(tmp_path / "web.db"),
            "TRADINGAGENTS_CACHE_DIR": str(tmp_path / "cache"),
        },
        load_env_files=False,
    )


@pytest.fixture
def web_repository(web_settings: AppSettings) -> RunRepository:
    upgrade_database(web_settings)
    return RunRepository(web_settings)


@pytest.fixture
def web_service(
    web_settings: AppSettings,
    web_repository: RunRepository,
) -> AnalysisService:
    return AnalysisService(
        web_settings,
        repository=web_repository,
        eligibility_resolver=_equity_resolver,
    )


@pytest.fixture
def anyio_backend():
    if importlib.util.find_spec("uvloop") is not None:
        return "asyncio", {"use_uvloop": True}
    return "asyncio"


@pytest.fixture
async def web_client(
    web_settings: AppSettings,
    web_service: AnalysisService,
):
    transport = httpx.ASGITransport(
        app=create_app(web_settings, service=web_service)
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client
