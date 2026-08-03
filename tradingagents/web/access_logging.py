"""Narrow Uvicorn access-log policy for bundled static assets."""

from __future__ import annotations

import copy
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG


class SuccessfulStaticAssetFilter(logging.Filter):
    """Drop only successful cacheable requests for bundled `/assets/*` files."""

    def filter(self, record: logging.LogRecord) -> bool:
        request = _access_request(record.args)
        if request is None:
            return True
        method, path, status = request
        return not (
            method in {"GET", "HEAD"}
            and path.startswith("/assets/")
            and status in {200, 304}
        )


def uvicorn_log_config() -> dict[str, Any]:
    """Return an isolated Uvicorn config with the static-success filter."""

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["successful_static_assets"] = {
        "()": SuccessfulStaticAssetFilter,
    }
    access_handler = config["handlers"]["access"]
    access_handler["filters"] = [
        *access_handler.get("filters", []),
        "successful_static_assets",
    ]
    return config


def _access_request(args: Any) -> tuple[str, str, int] | None:
    if not isinstance(args, tuple) or len(args) < 5:
        return None
    method = args[1]
    path = args[2]
    status = args[4]
    if not isinstance(method, str) or not isinstance(path, str):
        return None
    try:
        normalized_status = int(status)
    except (TypeError, ValueError):
        return None
    return method.upper(), path, normalized_status
