"""Scoped collection progress; the application owns logging and persistence."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

ProgressHandler = Callable[[str, Literal["started", "completed"]], None]
_handler: ContextVar[ProgressHandler | None] = ContextVar("collection_progress", default=None)


@contextmanager
def collection_progress(handler: ProgressHandler) -> Iterator[None]:
    token = _handler.set(handler)
    try:
        yield
    finally:
        _handler.reset(token)


def report_collection_progress(domain: str, phase: Literal["started", "completed"]) -> None:
    handler = _handler.get()
    if handler is not None:
        handler(domain, phase)
