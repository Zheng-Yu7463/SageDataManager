from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.config import settings


@dataclass(frozen=True)
class FileAccessClaims:
    grant_id: UUID


@dataclass(frozen=True)
class SessionClaims:
    username: str
    generation: int


@dataclass(frozen=True)
class UploadClaims:
    upload_id: UUID
    asset_id: UUID
    target_subdirectory: str
    username: str


class TokenSigningConfigurationError(ValueError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(value: str) -> str:
    secret = settings.auth_session_secret or settings.fixed_account_password
    if not secret:
        raise TokenSigningConfigurationError("服务器尚未配置认证签名密钥。")
    return _encode(hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest())


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, n_value, r_value, p_value, salt_value, digest_value = encoded_hash.split("$")
        if (scheme, n_value, r_value, p_value) != ("scrypt", "16384", "8", "1"):
            return False
        expected = _decode(digest_value)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_decode(salt_value),
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session_token(username: str, generation: int = 0) -> str:
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.auth_session_ttl_seconds)
    payload = _encode(
        json.dumps(
            {
                "kind": "session",
                "username": username,
                "generation": generation,
                "exp": expires_at.timestamp(),
            }
        ).encode()
    )
    return f"{payload}.{_sign(payload)}"


def read_session_claims(token: str) -> SessionClaims | None:
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        value = json.loads(_decode(payload))
        generation = value.get("generation", 0)
        if (
            value.get("kind") not in (None, "session")
            or not isinstance(value.get("username"), str)
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
            or datetime.now(UTC).timestamp() >= value["exp"]
        ):
            return None
        return SessionClaims(username=value["username"], generation=generation)
    except (ValueError, json.JSONDecodeError, TypeError, KeyError):
        return None


def read_session_token(token: str) -> str | None:
    claims = read_session_claims(token)
    return claims.username if claims else None


def create_file_access_token(grant_id: UUID, expires_at: datetime) -> str:
    payload = _encode(
        json.dumps(
            {
                "kind": "file_access",
                "grant_id": str(grant_id),
                "exp": expires_at.timestamp(),
            }
        ).encode()
    )
    return f"{payload}.{_sign(payload)}"


def read_file_access_token(token: str) -> FileAccessClaims | None:
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        value = json.loads(_decode(payload))
        if (
            value.get("kind") != "file_access"
            or not isinstance(value.get("grant_id"), str)
            or datetime.now(UTC).timestamp() >= value["exp"]
        ):
            return None
        return FileAccessClaims(grant_id=UUID(value["grant_id"]))
    except (ValueError, json.JSONDecodeError, TypeError, KeyError):
        return None


def create_upload_token(
    upload_id: UUID, asset_id: UUID, target_subdirectory: str, username: str
) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.upload_ticket_ttl_seconds)
    payload = _encode(
        json.dumps(
            {
                "kind": "upload",
                "upload_id": str(upload_id),
                "asset_id": str(asset_id),
                "target_subdirectory": target_subdirectory,
                "username": username,
                "exp": expires_at.timestamp(),
            }
        ).encode()
    )
    return f"{payload}.{_sign(payload)}", expires_at


def read_upload_token(token: str) -> UploadClaims | None:
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        value = json.loads(_decode(payload))
        if (
            value.get("kind") != "upload"
            or not isinstance(value.get("upload_id"), str)
            or not isinstance(value.get("asset_id"), str)
            or not isinstance(value.get("target_subdirectory"), str)
            or not isinstance(value.get("username"), str)
            or datetime.now(UTC).timestamp() >= value["exp"]
        ):
            return None
        return UploadClaims(
            upload_id=UUID(value["upload_id"]),
            asset_id=UUID(value["asset_id"]),
            target_subdirectory=value["target_subdirectory"],
            username=value["username"],
        )
    except (ValueError, json.JSONDecodeError, TypeError, KeyError):
        return None
