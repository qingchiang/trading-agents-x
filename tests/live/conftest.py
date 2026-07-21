"""Reporting helpers for opt-in live endpoint contracts."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import pytest


@dataclass
class _EndpointRecord:
    endpoint: str
    status: str
    source: str
    last_observation: str
    latency_ms: int
    detail: str = ""


_ENDPOINT_RECORDS: list[_EndpointRecord] = []


class _EndpointProbe:
    def __init__(self, endpoint: str, *, source: str):
        self.endpoint = endpoint
        self.source = source
        self.last_observation = "n/a"
        self.started = 0.0

    def __enter__(self) -> _EndpointProbe:
        self.started = time.perf_counter()
        return self

    def observe(self, *, source: object, last_observation: object = "n/a") -> None:
        self.source = str(source).strip() or "unknown"
        self.last_observation = str(last_observation).strip() or "n/a"

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        _ENDPOINT_RECORDS.append(
            _EndpointRecord(
                endpoint=self.endpoint,
                status="passed" if exc_type is None else "failed",
                source=self.source,
                last_observation=self.last_observation,
                latency_ms=round((time.perf_counter() - self.started) * 1000),
                detail="" if exc_type is None else exc_type.__name__,
            )
        )
        return False


@pytest.fixture
def live_endpoint():
    """Return a context-manager factory that records one endpoint contract."""

    return _EndpointProbe


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Always show the endpoint audit after an explicitly enabled live run."""
    if os.environ.get("RUN_LIVE_DATA_TESTS") != "1" or not _ENDPOINT_RECORDS:
        return
    terminalreporter.section("live endpoint audit")
    for record in _ENDPOINT_RECORDS:
        detail = f" detail={record.detail}" if record.detail else ""
        terminalreporter.write_line(
            f"{record.status:6} endpoint={record.endpoint} source={record.source} "
            f"last={record.last_observation} latency_ms={record.latency_ms}{detail}"
        )
