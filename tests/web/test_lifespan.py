from __future__ import annotations

import pytest

from tradingagents.web import create_app


class _Maintenance:
    def __init__(self, *, fail: bool = False):
        self.calls = 0
        self.fail = fail

    def run_once(self) -> int:
        self.calls += 1
        if self.fail:
            raise RuntimeError("private fixture details")
        return 0


@pytest.mark.anyio
async def test_web_startup_runs_archive_maintenance_once(
    web_settings,
    web_service,
) -> None:
    maintenance = _Maintenance()
    app = create_app(
        web_settings,
        service=web_service,
        maintenance=maintenance,
    )

    async with app.router.lifespan_context(app):
        assert maintenance.calls == 1


@pytest.mark.anyio
async def test_web_startup_continues_when_archive_maintenance_fails(
    web_settings,
    web_service,
    caplog,
) -> None:
    maintenance = _Maintenance(fail=True)
    app = create_app(
        web_settings,
        service=web_service,
        maintenance=maintenance,
    )

    async with app.router.lifespan_context(app):
        assert maintenance.calls == 1
    assert "RuntimeError" in caplog.text
    assert "private fixture details" not in caplog.text
