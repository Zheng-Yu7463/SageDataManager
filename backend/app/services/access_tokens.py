from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import settings
from app.domain.activity import ActivityAction
from app.domain.models import PersonalAccessToken, User
from app.domain.schemas import (
    AccessTokenCreatedResponse,
    AccessTokenCreateRequest,
    AccessTokenSummary,
)
from app.services.activities import record_activity


class AccessTokenNotFoundError(Exception):
    pass


class AccessTokenConfigurationError(Exception):
    pass


def _signing_secret() -> str:
    signing_secret = settings.auth_session_secret or settings.fixed_account_password
    if not signing_secret:
        raise AccessTokenConfigurationError("服务器尚未配置访问令牌签名密钥。")
    return signing_secret


def _token_secret_hash(secret: str) -> str:
    return hmac.new(_signing_secret().encode(), secret.encode(), hashlib.sha256).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def access_token_summary(token: PersonalAccessToken) -> AccessTokenSummary:
    return AccessTokenSummary(
        id=token.id,
        name=token.name,
        token_prefix=f"sdm_pat_{token.public_id}",
        scopes=token.scopes,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        revoked_at=token.revoked_at,
    )


def create_access_token(
    session: Session,
    user: User,
    payload: AccessTokenCreateRequest,
) -> AccessTokenCreatedResponse:
    public_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    plaintext = f"sdm_pat_{public_id}_{secret}"
    token = PersonalAccessToken(
        user=user,
        public_id=public_id,
        name=payload.name,
        secret_hash=_token_secret_hash(secret),
        scopes=payload.scopes,
        expires_at=datetime.now(UTC) + timedelta(days=payload.expires_in_days),
    )
    session.add(token)
    record_activity(
        session,
        actor=user,
        action=ActivityAction.CREATED_ACCESS_TOKEN,
        description=f"创建了 AI 访问令牌「{payload.name}」",
    )
    session.flush()
    return AccessTokenCreatedResponse(**access_token_summary(token).model_dump(), token=plaintext)


def list_access_tokens(session: Session, user: User) -> list[AccessTokenSummary]:
    tokens = session.scalars(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == user.id)
        .order_by(PersonalAccessToken.created_at.desc())
    ).all()
    return [access_token_summary(token) for token in tokens]


def revoke_access_token(session: Session, user: User, token_id: UUID) -> AccessTokenSummary:
    token = session.scalar(
        select(PersonalAccessToken).where(
            PersonalAccessToken.id == token_id,
            PersonalAccessToken.user_id == user.id,
        )
    )
    if not token:
        raise AccessTokenNotFoundError
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        record_activity(
            session,
            actor=user,
            action=ActivityAction.REVOKED_ACCESS_TOKEN,
            description=f"撤销了 AI 访问令牌「{token.name}」",
        )
    session.flush()
    return access_token_summary(token)


def authenticate_access_token(session: Session, plaintext: str) -> PersonalAccessToken | None:
    parts = plaintext.split("_", 3)
    if len(parts) != 4 or parts[:2] != ["sdm", "pat"]:
        return None
    public_id, secret = parts[2], parts[3]
    candidate_hash = _token_secret_hash(secret)
    token = session.scalar(
        select(PersonalAccessToken).where(PersonalAccessToken.public_id == public_id)
    )
    stored_hash = token.secret_hash if token else "0" * 64
    secret_matches = hmac.compare_digest(stored_hash, candidate_hash)
    now = datetime.now(UTC)
    if (
        not token
        or not secret_matches
        or token.revoked_at is not None
        or _aware(token.expires_at) <= now
        or not token.user.is_active
    ):
        return None
    return token


def record_access_token_use(session: Session, token: PersonalAccessToken) -> None:
    now = datetime.now(UTC)
    interval = timedelta(seconds=max(0, settings.agent_token_last_used_interval_seconds))
    cutoff = now - interval
    if token.last_used_at is not None and _aware(token.last_used_at) >= cutoff:
        return

    result = session.execute(
        update(PersonalAccessToken)
        .where(
            PersonalAccessToken.id == token.id,
            or_(
                PersonalAccessToken.last_used_at.is_(None),
                PersonalAccessToken.last_used_at < cutoff,
            ),
        )
        .values(last_used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        session.commit()
        set_committed_value(token, "last_used_at", now)
