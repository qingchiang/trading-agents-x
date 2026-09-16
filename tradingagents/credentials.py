"""Execution-scoped secrets, excluded from graph state and configuration snapshots."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

_credentials: ContextVar[Mapping[str, str]] = ContextVar(
    "credentials", default=MappingProxyType({})
)


def credential(name: str) -> str | None:
    return _credentials.get().get(name)


@contextmanager
def use_credentials(values: Mapping[str, str]) -> Iterator[None]:
    token = _credentials.set(MappingProxyType(dict(values)))
    try:
        yield
    finally:
        _credentials.reset(token)


def credential_redactor():
    """Capture a redactor for the current immutable execution credentials."""
    secrets = sorted(set(_credentials.get().values()), key=len, reverse=True)

    def redact(value: str) -> str:
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    return redact
