"""Shared AkShare loading, symbol conversion, retry, and typed errors."""

from __future__ import annotations

import importlib
import json
import time
from http.client import RemoteDisconnected

import requests

from ..errors import (
    VendorError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from ..symbol_utils import infer_mainland_equity_suffix, normalize_symbol

REQUEST_TIMEOUT = 15.0
MAX_ATTEMPTS = 2
BASE_RETRY_DELAY = 0.5


class AkShareUnavailableError(VendorNotConfiguredError):
    """AkShare is selected but cannot be imported or initialized."""


class AkShareRateLimitError(VendorRateLimitError):
    """An AkShare upstream source throttled the request."""


class AkShareRequestError(VendorError):
    """An AkShare upstream source failed before returning usable data."""


class AkShareSchemaError(AkShareRequestError):
    """An AkShare response no longer matches the expected tabular schema."""


def load_akshare():
    """Import AkShare lazily and translate broken installs to a typed error."""
    try:
        return importlib.import_module("akshare")
    except Exception as exc:  # noqa: BLE001 - binary/optional import failures vary
        raise AkShareUnavailableError(
            "AkShare could not be imported. Reinstall the project runtime "
            f"dependencies; original error: {type(exc).__name__}: {exc}"
        ) from exc


def canonical_a_share(symbol: str) -> tuple[str, str, str]:
    """Return ``(canonical, plain_code, exchange_prefix)`` for .SS/.SZ equity."""
    canonical = normalize_symbol(symbol)
    suffix = canonical[-3:] if canonical.endswith((".SS", ".SZ")) else ""
    code = canonical[:-3] if suffix else ""
    expected = infer_mainland_equity_suffix(code)
    if expected is None:
        raise ValueError(
            "AkShare A-share market data requires a supported six-digit Shanghai "
            f"or Shenzhen equity code, got {symbol!r}. Indices, funds, bonds, and "
            "other security types are out of scope."
        )
    if suffix != expected:
        raise ValueError(
            f"Exchange suffix mismatch for {symbol!r}: code {code} requires "
            f"{expected}, not {suffix or '<missing>'}."
        )
    return canonical, code, "sh" if suffix == ".SS" else "sz"


def _is_rate_limit(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    message = str(exc).casefold()
    return "429" in message or "rate limit" in message or "too many requests" in message


def _is_retryable(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            requests.RequestException,
            RemoteDisconnected,
            TimeoutError,
            json.JSONDecodeError,
        ),
    ) or _is_rate_limit(exc)


def call_with_retry(func, /, *args, label: str, **kwargs):
    """Call one AkShare endpoint with bounded retries and typed failures."""
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - normalize third-party failures
            last_exc = exc
            if attempt + 1 >= MAX_ATTEMPTS or not _is_retryable(exc):
                break
            time.sleep(BASE_RETRY_DELAY * (2**attempt))

    assert last_exc is not None
    if _is_rate_limit(last_exc):
        raise AkShareRateLimitError(f"{label} rate limited the request.") from last_exc
    raise AkShareRequestError(
        f"{label} request failed: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc
