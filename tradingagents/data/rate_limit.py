"""Focused rate-limit control for bounded collection journeys."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_STOP_ON_RATE_LIMIT: ContextVar[bool] = ContextVar("stop_on_rate_limit", default=False)


@contextmanager
def stop_on_rate_limit_scope(enabled: bool) -> Iterator[None]:
    """Make an explicitly bounded route stop instead of retrying a 429."""
    token = _STOP_ON_RATE_LIMIT.set(enabled)
    try:
        yield
    finally:
        _STOP_ON_RATE_LIMIT.reset(token)


def stop_on_rate_limit_requested() -> bool:
    """Whether the active route must surface the first provider 429."""
    return _STOP_ON_RATE_LIMIT.get()
