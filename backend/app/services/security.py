from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

from app.core.config import settings


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(value: str) -> str:
    secret = settings.auth_session_secret or settings.fixed_account_password
    return _encode(hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest())


def create_session_token(username: str) -> str:
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.auth_session_ttl_seconds)
    payload = _encode(json.dumps({"username": username, "exp": expires_at.timestamp()}).encode())
    return f"{payload}.{_sign(payload)}"


def read_session_token(token: str) -> str | None:
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        value = json.loads(_decode(payload))
        if (
            not isinstance(value.get("username"), str)
            or datetime.now(UTC).timestamp() >= value["exp"]
        ):
            return None
        return value["username"]
    except (ValueError, json.JSONDecodeError, TypeError):
        return None
