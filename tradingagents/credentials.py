"""Execution-scoped secrets, excluded from graph state and configuration snapshots."""

import logging
import threading
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from urllib.parse import quote, quote_plus

_logging_lock = threading.Lock()
_SAFE_DIAGNOSTIC = "_tradingagents_safe_diagnostic"

_credentials: ContextVar[Mapping[str, str]] = ContextVar(
    "credentials", default=MappingProxyType({})
)


def credential(name: str) -> str | None:
    return _credentials.get().get(name)


@contextmanager
def use_credentials(values: Mapping[str, str]) -> Iterator[None]:
    _install_log_redaction()
    token = _credentials.set(MappingProxyType(dict(values)))
    try:
        yield
    except Exception as exc:
        # Preserve exception types for Python callers, but capture a safe diagnostic
        # before this attempt's credentials leave scope or are replaced in the DB.
        diagnostic = getattr(exc, _SAFE_DIAGNOSTIC, None) or "".join(
            traceback.format_exception(exc)
        )
        setattr(exc, _SAFE_DIAGNOSTIC, credential_redactor()(diagnostic))
        raise
    finally:
        _credentials.reset(token)


def credential_redactor():
    """Capture a redactor for the current immutable execution credentials."""
    secrets = sorted(
        {
            variant
            for value in _credentials.get().values()
            if value
            for variant in (value, quote(value, safe=""), quote_plus(value))
        },
        key=len,
        reverse=True,
    )

    def redact(value: str) -> str:
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    return redact


def safe_failure_diagnostic(exc: Exception) -> str:
    """Never format an untrusted exception after its credentials leave scope."""
    return getattr(exc, _SAFE_DIAGNOSTIC, None) or type(exc).__name__


def _install_log_redaction() -> None:
    """Compose the process logging factory; secrets remain in task-local memory.

    Redact at record creation so queued handlers and worker threads cannot format
    raw provider errors after the credential context has disappeared. No secrets
    are retained by the factory, and logging outside a scope is unchanged.
    """
    with _logging_lock:
        original = logging.getLogRecordFactory()
        if getattr(original, "_tradingagents_redacts_credentials", False):
            return

        def scoped_record(*args, **kwargs):
            record = original(*args, **kwargs)
            if not _credentials.get():
                return record
            redact = credential_redactor()
            record.msg = redact(record.getMessage())
            record.args = ()
            if record.exc_info:
                record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = redact(record.exc_text)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
            return record

        scoped_record._tradingagents_redacts_credentials = True
        logging.setLogRecordFactory(scoped_record)
