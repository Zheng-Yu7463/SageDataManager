from __future__ import annotations

import hmac

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.models import User
from app.domain.schemas import AccountCreateRequest, AccountSummary, AccountUpdateRequest
from app.services.security import create_session_token, hash_password, verify_password


class AccountLoginError(Exception):
    pass


class AccountConflictError(Exception):
    pass


class AccountNotFoundError(Exception):
    pass


class AccountSetupConflictError(Exception):
    pass


class AccountAuthenticationConfigurationError(Exception):
    pass


def account_summary(user: User) -> AccountSummary:
    if not user.username:
        raise AccountLoginError("账号尚未初始化。")
    return AccountSummary(
        id=user.id,
        username=user.username,
        name=user.name,
        email=user.email,
        role=user.role,
        upload_username=user.username,
        is_active=user.is_active,
        is_instance_owner=user.is_instance_owner,
    )


def list_admin_accounts(session: Session) -> list[AccountSummary]:
    users = session.scalars(
        select(User)
        .where(User.username.is_not(None), User.role == "admin")
        .order_by(User.is_active.desc(), User.username)
    ).all()
    return [account_summary(user) for user in users]


def instance_setup_status(session: Session) -> tuple[bool, bool]:
    initialized = session.scalar(select(User.id).limit(1)) is not None
    authentication_ready = bool(
        settings.auth_session_secret
        or (initialized and settings.fixed_account_password)
    )
    return initialized, authentication_ready


def get_instance_owner(session: Session) -> User | None:
    return session.scalar(select(User).where(User.is_instance_owner.is_(True)))


def initialize_admin_account(
    session: Session, payload: AccountCreateRequest
) -> tuple[AccountSummary, str]:
    initialized, authentication_ready = instance_setup_status(session)
    if initialized:
        raise AccountSetupConflictError("实例已经完成初始化，请使用管理员账号登录。")
    if not authentication_ready:
        raise AccountAuthenticationConfigurationError(
            "服务器尚未配置 SAGE_AUTH_SESSION_SECRET。"
        )
    user = _new_admin(payload, is_instance_owner=True)
    session.add(user)
    session.flush()
    return account_summary(user), create_session_token(user.username or "")


def create_admin_account(session: Session, payload: AccountCreateRequest) -> AccountSummary:
    username = payload.username.strip().lower()
    email = payload.email.strip().lower()
    if session.scalar(select(User.id).where(or_(User.username == username, User.email == email))):
        raise AccountConflictError
    user = _new_admin(payload, is_instance_owner=False)
    session.add(user)
    session.flush()
    return account_summary(user)


def _new_admin(payload: AccountCreateRequest, *, is_instance_owner: bool) -> User:
    return User(
        username=payload.username.strip().lower(),
        name=payload.name.strip(),
        email=payload.email.strip().lower(),
        role="admin",
        password_hash=hash_password(payload.password),
        is_active=True,
        is_instance_owner=is_instance_owner,
    )


def update_admin_account(
    session: Session, username: str, payload: AccountUpdateRequest, *, actor: User
) -> AccountSummary:
    user = session.scalar(select(User).where(User.username == username))
    if not user:
        raise AccountNotFoundError
    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    if payload.is_active is not None:
        if user.id == actor.id and not payload.is_active:
            raise AccountConflictError("不能停用当前登录账号。")
        user.is_active = payload.is_active
    session.flush()
    return account_summary(user)


def login_account(session: Session, username: str, password: str) -> tuple[AccountSummary, str]:
    user = get_active_account(session, username)
    if not user or user.role != "admin":
        raise AccountLoginError("账号不可用。")
    password_matches = bool(user.password_hash and verify_password(password, user.password_hash))
    legacy_password_matches = bool(
        not user.password_hash
        and settings.fixed_account_password
        and hmac.compare_digest(password, settings.fixed_account_password)
    )
    if not password_matches and not legacy_password_matches:
        raise AccountLoginError("账号或密码错误。")
    if not (settings.auth_session_secret or settings.fixed_account_password):
        raise AccountAuthenticationConfigurationError(
            "服务器尚未配置 SAGE_AUTH_SESSION_SECRET。"
        )
    if legacy_password_matches:
        user.password_hash = hash_password(password)
        session.flush()
    return account_summary(user), create_session_token(username)


def get_active_account(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == username, User.is_active.is_(True)))
