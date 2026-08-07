from __future__ import annotations

import hmac

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.models import User
from app.domain.schemas import AccountSummary
from app.services.security import create_session_token

FIXED_USERNAMES = ("yukai", "zhengyu", "zhourongyang", "fengxuehan", "chenshangyu", "bisheng")


class AccountLoginError(Exception):
    pass


def ensure_fixed_accounts(session: Session) -> list[User]:
    users: list[User] = []
    for username in FIXED_USERNAMES:
        email = f"{username}@sage.lab"
        user = session.scalar(
            select(User).where(or_(User.username == username, User.email == email))
        )
        if not user:
            user = User(username=username, name=username, email=email, role="admin", is_active=True)
            session.add(user)
        else:
            user.username = username
            user.role = "admin"
            user.is_active = True
        users.append(user)
    session.flush()
    return users


def account_summary(user: User) -> AccountSummary:
    if not user.username:
        raise AccountLoginError("账号尚未初始化。")
    return AccountSummary(
        id=user.id,
        username=user.username,
        name=user.name,
        role=user.role,
        upload_username=user.username,
    )


def login_account(session: Session, username: str, password: str) -> tuple[AccountSummary, str]:
    if not settings.fixed_account_password:
        raise AccountLoginError("服务器尚未配置固定账号密码。")
    if not hmac.compare_digest(password, settings.fixed_account_password):
        raise AccountLoginError("账号或密码错误。")

    ensure_fixed_accounts(session)
    user = get_active_account(session, username)
    if not user or user.role != "admin":
        raise AccountLoginError("账号不可用。")
    return account_summary(user), create_session_token(username)


def get_active_account(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == username, User.is_active.is_(True)))
