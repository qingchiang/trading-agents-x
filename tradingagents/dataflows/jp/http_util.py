"""Shared stdlib HTTP for the keyless JP feeds (Google News, TDnet).

Owns the identified User-Agent and the one fetch *policy* these feeds share: send
the request, back off once on a 429 (honouring ``Retry-After``) for ordinary
requests, or surface the first 429 for bounded routes, and degrade to None on any
other network/HTTP error. Each feed keeps only what actually differs — constructing
its ``Request`` and parsing the body (XML vs HTML). (Reddit's feed carries its own
copy — it's an upstream file this fork doesn't edit.)
"""

from __future__ import annotations

import http.client
import logging
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tradingagents.version import IDENTIFIED_USER_AGENT

from ..errors import VendorRateLimitError
from ..rate_limit import stop_on_rate_limit_requested

logger = logging.getLogger(__name__)

# Identified User-Agent for this fork (served a plain descriptive token, as with
# Reddit/Google-News RSS). Points at the fork so a site operator can reach us.
USER_AGENT = IDENTIFIED_USER_AGENT


def retry_after_seconds(exc: HTTPError) -> float | None:
    """Seconds to wait from a 429 ``Retry-After`` header, capped at 30s."""
    try:
        val = exc.headers.get("Retry-After")
        return min(float(val), 30.0) if val else None
    except (ValueError, TypeError, AttributeError):
        return None


def fetch_bytes(req: Request, timeout: float, label: str, _retry: bool = True) -> bytes | None:
    """Send ``req`` and return the response body, or None on any failure.

    Ordinary routes back off once on a 429 (honouring ``Retry-After``); bounded
    routes raise ``VendorRateLimitError`` immediately. Other HTTP/network failures
    degrade to None. ``label`` identifies the feed and key in log lines. Callers own
    request construction and body parsing.
    """
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as exc:
        if exc.code == 429:
            if stop_on_rate_limit_requested():
                raise VendorRateLimitError(f"{label} rate limited") from exc
            if _retry:
                wait = retry_after_seconds(exc) or 5.0
                logger.warning("%s: 429 — backing off %.1fs then retrying once", label, wait)
                time.sleep(wait)
                return fetch_bytes(req, timeout, label, _retry=False)
        logger.warning("%s: fetch failed: %s", label, exc)
        return None
    except (OSError, http.client.HTTPException) as exc:
        logger.warning("%s: fetch failed: %s", label, exc)
        return None
