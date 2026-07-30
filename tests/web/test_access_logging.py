from __future__ import annotations

import logging

import pytest

from tradingagents.web.access_logging import (
    SuccessfulStaticAssetFilter,
    uvicorn_log_config,
)


@pytest.mark.parametrize(
    ("method", "path", "status", "expected"),
    [
        ("GET", "/assets/index.js", 200, False),
        ("HEAD", "/assets/index.css", 304, False),
        ("GET", "/assets/missing.js", 404, True),
        ("GET", "/assets/broken.js", 500, True),
        ("POST", "/assets/index.js", 200, True),
        ("GET", "/api/v1/runs", 200, True),
        ("GET", "/api/v1/runs", 304, True),
        ("GET", "/api/v1/runs", 307, True),
    ],
)
def test_successful_static_asset_filter_is_narrow(
    method: str,
    path: str,
    status: int,
    expected: bool,
) -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1000", method, path, "1.1", status),
        exc_info=None,
    )

    assert SuccessfulStaticAssetFilter().filter(record) is expected


def test_uvicorn_log_config_installs_filter_without_mutating_default() -> None:
    first = uvicorn_log_config()
    first["handlers"]["access"]["filters"].append("fixture")
    second = uvicorn_log_config()

    assert second["handlers"]["access"]["filters"] == [
        "successful_static_assets"
    ]
