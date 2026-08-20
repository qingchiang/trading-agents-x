from __future__ import annotations

from pathlib import Path

import httpx2 as httpx
import pytest

from tradingagents.application.repository import RunRepository
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.persistence import upgrade_database
from tradingagents.web import create_app


def _lan_app(tmp_path: Path):
    settings = AppSettings.from_env(
        environ={
            "TRADINGAGENTS_HOME": str(tmp_path),
            "TRADINGAGENTS_DATABASE_PATH": str(tmp_path / "lan.db"),
            "TRADINGAGENTS_LAN_ENABLED": "true",
            "TRADINGAGENTS_LAN_TOKEN": "local-lan-secret",
            "TRADINGAGENTS_SESSION_SECRET": "separate-session-secret",
        },
        load_env_files=False,
    )
    upgrade_database(settings)
    repository = RunRepository(settings)
    service = AnalysisService(
        settings,
        repository=repository,
        eligibility_resolver=lambda ticker: {
            "symbol": ticker,
            "quote_type": "EQUITY",
        },
    )
    return create_app(settings, service=service), repository


@pytest.mark.anyio
async def test_lan_mode_uses_token_exchange_and_strict_cookie(
    tmp_path: Path,
) -> None:
    app, _repository = _lan_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        assert (await client.get("/api/v1/health")).status_code == 200
        assert (await client.get("/api/v1/capabilities")).status_code == 401
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={"token": "wrong-secret"},
            )
        ).status_code == 401

        login = await client.post(
            "/api/v1/auth/login",
            json={"token": "local-lan-secret"},
        )

        assert login.status_code == 200
        cookie = login.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert (await client.get("/api/v1/capabilities")).status_code == 200


@pytest.mark.anyio
async def test_lan_mutations_require_same_origin(tmp_path: Path) -> None:
    app, repository = _lan_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        await client.post(
            "/api/v1/auth/login",
            json={"token": "local-lan-secret"},
        )
        payload = {
            "ticker": "NVDA",
            "analysis_date": "2026-07-24",
        }

        rejected = await client.post(
            "/api/v1/runs",
            json=payload,
            headers={"Origin": "http://attacker.example"},
        )
        accepted = await client.post(
            "/api/v1/runs",
            json=payload,
            headers={"Origin": "http://testserver"},
        )

        assert rejected.status_code == 403
        assert accepted.status_code == 202
        assert len(repository.list_runs().items) == 1
        assert "local-lan-secret" not in str(
            repository.list_runs().items[0].config_snapshot
        )


@pytest.mark.anyio
async def test_invalid_login_body_never_reflects_token(
    tmp_path: Path,
) -> None:
    app, _repository = _lan_app(tmp_path)
    private_token = "x" * 5000
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"token": private_token},
        )

        assert response.status_code == 422
        assert private_token not in response.text
