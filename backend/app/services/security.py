from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import settings


@dataclass(frozen=True)
class FileAccessClaims:
    file_id: UUID
    mode: str
    username: str


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


def create_file_access_token(file_id: UUID, mode: str, username: str) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.file_access_ttl_seconds)
    payload = _encode(
        json.dumps(
            {
                "file_id": str(file_id),
                "mode": mode,
                "username": username,
                "exp": expires_at.timestamp(),
            }
        ).encode()
    )
    return f"{payload}.{_sign(payload)}", expires_at


def read_file_access_token(token: str) -> FileAccessClaims | None:
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        value = json.loads(_decode(payload))
        if (
            not isinstance(value.get("file_id"), str)
            or value.get("mode") not in {"download", "preview"}
            or not isinstance(value.get("username"), str)
            or datetime.now(UTC).timestamp() >= value["exp"]
        ):
            return None
        return FileAccessClaims(
            file_id=UUID(value["file_id"]), mode=value["mode"], username=value["username"]
        )
    except (ValueError, json.JSONDecodeError, TypeError):
        return None
