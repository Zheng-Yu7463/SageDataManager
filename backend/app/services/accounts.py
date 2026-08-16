from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.activity import ActivityAction
from app.domain.models import AccountInvitation, FileAccessGrant, PersonalAccessToken, User
from app.domain.schemas import (
    AccountCreateRequest,
    AccountInvitationAcceptRequest,
    AccountInvitationCreatedResponse,
    AccountInvitationCreateRequest,
    AccountInvitationStatus,
    AccountSummary,
    AccountUpdateRequest,
)
from app.services.activities import record_activity
from app.services.security import create_session_token, hash_password, verify_password

InvitationPurpose = Literal["registration", "recovery"]


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


class AccountInvitationInvalidError(Exception):
    pass


class AccountInvitationValidationError(Exception):
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
        is_registered=user.is_registered,
        is_instance_owner=user.is_instance_owner,
    )


def list_admin_accounts(session: Session) -> list[AccountSummary]:
    users = session.scalars(
        select(User)
        .where(User.username.is_not(None), User.role == "admin")
        .order_by(User.is_active.desc(), User.is_registered.desc(), User.username)
    ).all()
    return [account_summary(user) for user in users]


def instance_setup_status(session: Session) -> tuple[bool, bool]:
    initialized = session.scalar(select(User.id).limit(1)) is not None
    authentication_ready = bool(
        settings.auth_session_secret or (initialized and settings.fixed_account_password)
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
        raise AccountAuthenticationConfigurationError("服务器尚未配置 SAGE_AUTH_SESSION_SECRET。")
    user = _new_admin(payload, is_instance_owner=True)
    session.add(user)
    record_activity(
        session,
        actor=user,
        action=ActivityAction.INITIALIZED_INSTANCE,
        description=f"创建并初始化了实例所有者账号 {user.username}",
    )
    session.flush()
    return account_summary(user), create_session_token(user.username or "", user.session_generation)


def _new_admin(payload: AccountCreateRequest, *, is_instance_owner: bool) -> User:
    return User(
        username=payload.username.strip().lower(),
        name=payload.name.strip(),
        email=payload.email.strip().lower(),
        role="admin",
        password_hash=hash_password(payload.password),
        is_active=True,
        is_registered=True,
        is_instance_owner=is_instance_owner,
    )


def _invitation_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _revoke_user_credentials(session: Session, user: User, now: datetime) -> None:
    session.execute(
        update(PersonalAccessToken)
        .where(
            PersonalAccessToken.user_id == user.id,
            PersonalAccessToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    session.execute(delete(FileAccessGrant).where(FileAccessGrant.user_id == user.id))


def _issue_invitation(
    session: Session,
    user: User,
    *,
    actor: User,
    purpose: InvitationPurpose,
) -> AccountInvitationCreatedResponse:
    now = datetime.now(UTC)
    session.execute(
        update(AccountInvitation)
        .where(
            AccountInvitation.user_id == user.id,
            AccountInvitation.accepted_at.is_(None),
            AccountInvitation.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = secrets.token_urlsafe(48)
    expires_at = now + timedelta(seconds=settings.account_invitation_ttl_seconds)
    session.add(
        AccountInvitation(
            user_id=user.id,
            created_by_id=actor.id,
            token_hash=_invitation_digest(token),
            purpose=purpose,
            expires_at=expires_at,
        )
    )
    action = (
        ActivityAction.ISSUED_ACCOUNT_INVITATION
        if purpose == "registration"
        else ActivityAction.ISSUED_ACCOUNT_RECOVERY
    )
    description = (
        f"为管理员账号 {user.username} 生成了注册链接"
        if purpose == "registration"
        else f"为管理员账号 {user.username} 生成了密码恢复链接"
    )
    record_activity(
        session,
        actor=actor,
        action=action,
        description=description,
    )
    session.flush()
    return AccountInvitationCreatedResponse(
        account=account_summary(user),
        registration_path=f"/register#token={token}",
        expires_at=expires_at,
        purpose=purpose,
    )


def create_admin_invitation(
    session: Session,
    payload: AccountInvitationCreateRequest,
    *,
    actor: User,
) -> AccountInvitationCreatedResponse:
    username = payload.username.strip().lower()
    if session.scalar(select(User.id).where(User.username == username)):
        raise AccountConflictError("账号名已被使用。")
    user = User(
        username=username,
        name=None,
        email=None,
        role="admin",
        password_hash=None,
        is_active=True,
        is_registered=False,
        is_instance_owner=False,
    )
    session.add(user)
    session.flush()
    return _issue_invitation(session, user, actor=actor, purpose="registration")


def renew_admin_invitation(
    session: Session,
    username: str,
    *,
    actor: User,
    purpose: InvitationPurpose,
) -> AccountInvitationCreatedResponse:
    user = session.scalar(
        select(User)
        .where(User.username == username.strip().lower(), User.role == "admin")
        .with_for_update()
    )
    if not user:
        raise AccountNotFoundError
    if purpose == "registration" and user.is_registered:
        raise AccountConflictError("账号已经完成注册。")
    if purpose == "recovery" and not user.is_registered:
        raise AccountConflictError("账号尚未完成注册，请重新生成注册链接。")
    if not user.is_active:
        raise AccountConflictError("账号已停用，不能生成邀请链接。")
    return _issue_invitation(session, user, actor=actor, purpose=purpose)


def get_account_invitation(session: Session, token: str) -> AccountInvitationStatus:
    invitation = session.scalar(
        select(AccountInvitation).where(
            AccountInvitation.token_hash == _invitation_digest(token),
            AccountInvitation.accepted_at.is_(None),
            AccountInvitation.revoked_at.is_(None),
            AccountInvitation.expires_at > datetime.now(UTC),
        )
    )
    if not invitation:
        raise AccountInvitationInvalidError
    user = session.get(User, invitation.user_id)
    if not user or not user.username or not user.is_active:
        raise AccountInvitationInvalidError
    if invitation.purpose == "registration" and user.is_registered:
        raise AccountInvitationInvalidError
    if invitation.purpose == "recovery" and not user.is_registered:
        raise AccountInvitationInvalidError
    return AccountInvitationStatus(
        username=user.username,
        expires_at=invitation.expires_at,
        purpose=invitation.purpose,
    )


def accept_account_invitation(
    session: Session,
    token: str,
    payload: AccountInvitationAcceptRequest,
) -> tuple[AccountSummary, str]:
    if not (settings.auth_session_secret or settings.fixed_account_password):
        raise AccountAuthenticationConfigurationError("服务器尚未配置 SAGE_AUTH_SESSION_SECRET。")
    candidate = session.scalar(
        select(AccountInvitation).where(
            AccountInvitation.token_hash == _invitation_digest(token),
            AccountInvitation.accepted_at.is_(None),
            AccountInvitation.revoked_at.is_(None),
            AccountInvitation.expires_at > datetime.now(UTC),
        )
    )
    if not candidate:
        raise AccountInvitationInvalidError
    user = session.get(User, candidate.user_id, with_for_update=True)
    if not user or not user.username or not user.is_active:
        raise AccountInvitationInvalidError
    invitation = session.scalar(
        select(AccountInvitation)
        .where(
            AccountInvitation.id == candidate.id,
            AccountInvitation.accepted_at.is_(None),
            AccountInvitation.revoked_at.is_(None),
            AccountInvitation.expires_at > datetime.now(UTC),
        )
        .with_for_update()
    )
    if not invitation:
        raise AccountInvitationInvalidError

    if invitation.purpose == "registration":
        if user.is_registered:
            raise AccountInvitationInvalidError
        if payload.name is None or payload.email is None:
            raise AccountInvitationValidationError("请填写显示名称和邮箱。")
        email = payload.email.strip().lower()
        if session.scalar(select(User.id).where(User.email == email, User.id != user.id)):
            raise AccountConflictError("邮箱已被使用。")
        user.name = payload.name.strip()
        user.email = email
        user.is_registered = True
    elif invitation.purpose == "recovery":
        if not user.is_registered:
            raise AccountInvitationInvalidError
    else:
        raise AccountInvitationInvalidError

    now = datetime.now(UTC)
    if invitation.purpose == "recovery":
        _revoke_user_credentials(session, user, now)
    user.password_hash = hash_password(payload.password)
    user.session_generation += 1
    invitation.accepted_at = now
    session.execute(
        update(AccountInvitation)
        .where(
            AccountInvitation.user_id == user.id,
            AccountInvitation.id != invitation.id,
            AccountInvitation.accepted_at.is_(None),
            AccountInvitation.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    action = (
        ActivityAction.REGISTERED_ACCOUNT
        if invitation.purpose == "registration"
        else ActivityAction.RESET_ACCOUNT_PASSWORD
    )
    description = (
        f"完成了管理员账号 {user.username} 的注册"
        if invitation.purpose == "registration"
        else f"重置了管理员账号 {user.username} 的密码"
    )
    record_activity(
        session,
        actor=user,
        action=action,
        description=description,
    )
    session.flush()
    return account_summary(user), create_session_token(user.username, user.session_generation)


def update_admin_account(
    session: Session, username: str, payload: AccountUpdateRequest, *, actor: User
) -> AccountSummary:
    user = session.scalar(select(User).where(User.username == username).with_for_update())
    if not user:
        raise AccountNotFoundError
    changed = False
    if payload.name is not None:
        if not user.is_registered:
            raise AccountConflictError("待注册账号的信息应由受邀者填写。")
        name = payload.name.strip()
        if user.name != name:
            user.name = name
            changed = True
    if payload.is_active is not None:
        if user.id == actor.id and not payload.is_active:
            raise AccountConflictError("不能停用当前登录账号。")
        if user.is_active != payload.is_active:
            user.is_active = payload.is_active
            user.session_generation += 1
            if not payload.is_active:
                _revoke_user_credentials(session, user, datetime.now(UTC))
            changed = True
    if changed:
        record_activity(
            session,
            actor=actor,
            action=ActivityAction.UPDATED_ACCOUNT,
            description=f"更新了管理员账号 {user.username}",
        )
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
        raise AccountAuthenticationConfigurationError("服务器尚未配置 SAGE_AUTH_SESSION_SECRET。")
    if legacy_password_matches:
        user.password_hash = hash_password(password)
        user.session_generation += 1
        session.flush()
    return account_summary(user), create_session_token(username, user.session_generation)


def get_active_account(session: Session, username: str) -> User | None:
    return session.scalar(
        select(User).where(
            User.username == username,
            User.is_active.is_(True),
            User.is_registered.is_(True),
        )
    )
