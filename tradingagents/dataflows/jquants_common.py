"""Shared J-Quants (Japanese market, API v2) helpers: auth, requests, codes.

J-Quants API v2 authenticates with a dashboard-issued API key sent in the
``x-api-key`` header — the v1 refresh-token/id-token exchange was discontinued,
so there is no token caching. Symbols are converted between the Yahoo-style
``9984.T`` ticker and J-Quants' numeric securities code (:func:`to_jquants_code`
/ :func:`from_jquants_code`).
"""

from __future__ import annotations

import os

import requests

from .errors import VendorNotConfiguredError, VendorRateLimitError
from .symbol_utils import tokyo_securities_base

JQUANTS_API_BASE = "https://api.jquants.com/v2"

# Network timeout (seconds) so a stalled request can't hang the CLI/agents.
REQUEST_TIMEOUT = 30


class JQuantsNotConfiguredError(VendorNotConfiguredError):
    """Raised when J-Quants is selected but ``JQUANTS_API_KEY`` is unset/rejected.

    A VendorNotConfiguredError (and thus a ValueError), so the routing layer's
    "vendor unavailable" handling treats it like any other unconfigured vendor.
    """
    pass


class JQuantsRateLimitError(VendorRateLimitError):
    """Raised when the J-Quants API rate limit is exceeded (HTTP 429)."""
    pass


def get_api_key() -> str:
    """Return the J-Quants v2 API key (``x-api-key``) from the environment."""
    key = os.getenv("JQUANTS_API_KEY")
    if not key:
        raise JQuantsNotConfiguredError(
            "JQUANTS_API_KEY environment variable is not set. Issue an API key "
            "from the J-Quants dashboard (Settings > API Key)."
        )
    return key


def to_jquants_code(symbol: str) -> str:
    """Map a Yahoo-style Tokyo ticker to its J-Quants securities code.

    ``9984.T`` -> ``9984``. A bare code is returned unchanged. v2 accepts the
    4-digit code (returning ordinary shares for dual-listed names).
    """
    s = symbol.strip().upper()
    if s.endswith(".T"):
        s = s[:-2]
    return s


def from_jquants_code(code: str) -> str:
    """Map a J-Quants securities code back to a Yahoo-style Tokyo ticker.

    v2 responses carry the 5-digit code (``99840``); the display ticker uses the
    4-digit form (``9984.T``).
    """
    return f"{tokyo_securities_base(code)}.T"


def _request(path: str, params: dict) -> dict:
    """GET ``path`` with the ``x-api-key`` header; map auth/rate-limit to typed errors."""
    resp = requests.get(
        f"{JQUANTS_API_BASE}{path}",
        params=params,
        headers={"x-api-key": get_api_key()},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 429:
        raise JQuantsRateLimitError(f"J-Quants rate limit exceeded for {path}.")
    if resp.status_code in (401, 403):
        raise JQuantsNotConfiguredError(
            f"J-Quants rejected the API key ({resp.status_code}) for {path}. "
            "Check JQUANTS_API_KEY."
        )
    resp.raise_for_status()
    return resp.json()


def fetch_records(path: str, params: dict, data_key: str) -> list[dict]:
    """Fetch all records under ``data_key``, following ``pagination_key`` pages."""
    records: list[dict] = []
    page_params = dict(params)
    while True:
        body = _request(path, page_params)
        records.extend(body.get(data_key, []))
        key = body.get("pagination_key")
        if not key:
            return records
        page_params = {**params, "pagination_key": key}


def memoized_fetch(cache: dict, key, path: str, params: dict, data_key: str) -> list[dict]:
    """``fetch_records`` for ``path``, memoized in the caller-owned ``cache``.

    The analyst tools fetch the same J-Quants window repeatedly within one run
    (the indicators tool loops over indicators; the four fundamental tools share
    one summary), so memoizing collapses those into a single rate-limited API
    call. The caller owns ``cache`` (a module-level dict) so each endpoint stays
    isolated and tests can clear it.
    """
    cached = cache.get(key)
    if cached is not None:
        return cached
    records = fetch_records(path, params, data_key)
    cache[key] = records
    return records
