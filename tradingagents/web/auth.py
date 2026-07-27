"""Optional single-token LAN authentication with signed session cookies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from fastapi import Request

from tradingagents.application.settings import AppSettings

COOKIE_NAME = "tradingagents_session"
SESSION_MAX_AGE = 12 * 60 * 60


class LanSessionManager:
    def __init__(self, settings: AppSettings):
        self.enabled = settings.lan_enabled
        self._token = (
            settings.lan_token.get_secret_value() if settings.lan_token else ""
        )
        session_secret = (
            settings.session_secret.get_secret_value()
            if settings.session_secret
            else self._token
        )
        self._secret = session_secret.encode()

    def authenticate_token(self, token: str) -> bool:
        return bool(self._token) and hmac.compare_digest(token, self._token)

    def issue(self) -> str:
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        payload = f"{timestamp}.{nonce}"
        signature = hmac.new(
            self._secret,
            payload.encode(),
            hashlib.sha256,
        ).digest()
        encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        return f"{payload}.{encoded}"

    def validate(self, value: str | None) -> bool:
        if not value or not self._secret:
            return False
        try:
            timestamp, nonce, supplied = value.split(".", 2)
            created = int(timestamp)
        except (TypeError, ValueError):
            return False
        now = int(time.time())
        if created > now + 60 or now - created > SESSION_MAX_AGE:
            return False
        payload = f"{timestamp}.{nonce}"
        expected = base64.urlsafe_b64encode(
            hmac.new(
                self._secret,
                payload.encode(),
                hashlib.sha256,
            ).digest()
        ).decode().rstrip("=")
        return hmac.compare_digest(supplied, expected)

    @staticmethod
    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            return True
        parsed = urlsplit(origin)
        return parsed.netloc.casefold() == request.headers.get(
            "host", ""
        ).casefold()
