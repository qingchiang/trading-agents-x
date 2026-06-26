"""Shared EDINET (Japanese statutory disclosure, API v2) helpers.

EDINET is the Financial Services Agency's electronic disclosure system. Its v2
API authenticates with a subscription key sent in the ``Ocp-Apim-Subscription-Key``
header (env ``EDINET_API_KEY``). The document-list endpoint is **date-keyed only**
— ``documents.json?date=YYYY-MM-DD&type=2`` returns every filing submitted that
day — so per-company queries iterate dates and filter by securities code.

This module backs :mod:`edinet_news` (per-ticker disclosure feed) today; the
same auth/request layer is intended to back full XBRL statement parsing later
(the deferred ``/fins/details`` alternative).
"""

from __future__ import annotations

import os

import requests

from .errors import VendorNotConfiguredError, VendorRateLimitError

EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"

# Network timeout (seconds) so a stalled request can't hang the CLI/agents.
REQUEST_TIMEOUT = 30


class EDINETNotConfiguredError(VendorNotConfiguredError):
    """Raised when EDINET is selected but ``EDINET_API_KEY`` is unset/rejected."""
    pass


class EDINETRateLimitError(VendorRateLimitError):
    """Raised when the EDINET API rate limit is exceeded (HTTP 429)."""
    pass


def get_api_key() -> str:
    """Return the EDINET v2 subscription key from the environment."""
    key = os.getenv("EDINET_API_KEY")
    if not key:
        raise EDINETNotConfiguredError(
            "EDINET_API_KEY environment variable is not set. Issue a subscription "
            "key from the EDINET API registration page (https://api.edinet-fsa.go.jp)."
        )
    return key


def _request(path: str, params: dict) -> dict:
    """GET ``path`` with the subscription-key header; map auth/rate-limit to typed errors."""
    resp = requests.get(
        f"{EDINET_API_BASE}{path}",
        params=params,
        headers={"Ocp-Apim-Subscription-Key": get_api_key()},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 429:
        raise EDINETRateLimitError(f"EDINET rate limit exceeded for {path}.")
    if resp.status_code in (401, 403):
        raise EDINETNotConfiguredError(
            f"EDINET rejected the subscription key ({resp.status_code}) for {path}. "
            "Check EDINET_API_KEY."
        )
    resp.raise_for_status()
    return resp.json()


def fetch_documents(date_str: str) -> list[dict]:
    """Return every document filed on ``date_str`` (``YYYY-MM-DD``).

    Uses ``type=2`` (metadata + document list). Dates with no filings (weekends,
    holidays) return an empty list rather than erroring.
    """
    body = _request("/documents.json", {"date": date_str, "type": 2})
    return body.get("results") or []
