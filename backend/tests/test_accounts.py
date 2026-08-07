import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import require_admin
from app.core.config import settings
from app.db.base import Base
from app.domain.models import User
from app.services.accounts import (
    FIXED_USERNAMES,
    AccountLoginError,
    ensure_fixed_accounts,
    login_account,
)
from app.services.security import read_session_token


def make_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_fixed_accounts_are_created_as_administrators() -> None:
    session = make_session()

    accounts = ensure_fixed_accounts(session)

    assert [account.username for account in accounts] == list(FIXED_USERNAMES)
    assert {account.role for account in accounts} == {"admin"}
    assert {account.email for account in accounts} == {
        f"{username}@sage.lab" for username in FIXED_USERNAMES
    }


def test_active_administrator_login_requires_shared_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "fixed_account_password", "test-password")
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")

    account, token = login_account(session, "zhengyu", "test-password")

    assert account.username == "zhengyu"
    assert account.upload_username == "zhengyu"
    assert require_admin(session, token).username == "zhengyu"
    with pytest.raises(HTTPException, match="请先登录"):
        require_admin(session, None)

    assert read_session_token(token) == "zhengyu"

    with pytest.raises(AccountLoginError, match="账号或密码错误"):
        login_account(session, "zhengyu", "wrong-password")


def test_preprovisioned_administrator_can_log_in(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "fixed_account_password", "test-password")
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    session.add(
        User(
            username="newadmin",
            name="New Admin",
            email="newadmin@sage.lab",
            role="admin",
            is_active=True,
        )
    )
    session.commit()

    account, token = login_account(session, "newadmin", "test-password")

    assert account.username == "newadmin"
    assert require_admin(session, token).username == "newadmin"
