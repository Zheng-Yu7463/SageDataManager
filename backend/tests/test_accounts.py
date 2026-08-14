import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import require_admin
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.models import User
from app.domain.schemas import AccountCreateRequest, AccountUpdateRequest
from app.main import app
from app.services.accounts import (
    AccountAuthenticationConfigurationError,
    AccountConflictError,
    AccountLoginError,
    AccountSetupConflictError,
    create_admin_account,
    initialize_admin_account,
    instance_setup_status,
    list_admin_accounts,
    login_account,
    update_admin_account,
)
from app.services.security import read_session_token, verify_password


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def account_request(username: str = "admin") -> AccountCreateRequest:
    return AccountCreateRequest(
        username=username,
        name="Instance Administrator",
        email=f"{username}@example.org",
        password="secure-password",
    )


def test_empty_instance_can_initialize_exactly_one_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "fixed_account_password", "")

    assert instance_setup_status(session) == (False, True)
    account, token = initialize_admin_account(session, account_request())
    session.commit()

    assert account.username == "admin"
    assert account.is_instance_owner is True
    assert read_session_token(token) == "admin"
    assert instance_setup_status(session) == (True, True)
    with pytest.raises(AccountSetupConflictError, match="已经完成初始化"):
        initialize_admin_account(session, account_request("secondadmin"))


def test_legacy_shared_password_cannot_initialize_an_empty_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "")
    monkeypatch.setattr(settings, "fixed_account_password", "legacy-password")

    assert instance_setup_status(session) == (False, False)
    with pytest.raises(
        AccountAuthenticationConfigurationError,
        match="SAGE_AUTH_SESSION_SECRET",
    ):
        initialize_admin_account(session, account_request())


def test_setup_api_is_public_and_closes_after_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "fixed_account_password", "")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        assert client.get("/api/auth/setup-status").json() == {
            "initialized": False,
            "authentication_ready": True,
        }
        created = client.post("/api/auth/setup", json=account_request().model_dump())
        repeated = client.post(
            "/api/auth/setup", json=account_request("secondadmin").model_dump()
        )

        assert created.status_code == 201
        assert created.json()["username"] == "admin"
        assert created.json()["is_instance_owner"] is True
        assert repeated.status_code == 409
        assert client.get("/api/auth/setup-status").json()["initialized"] is True
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_existing_non_admin_user_does_not_reopen_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    session.add(
        User(
            username="member",
            name="Member",
            email="member@example.org",
            role="member",
            is_active=True,
        )
    )
    session.commit()

    assert instance_setup_status(session)[0] is True
    with pytest.raises(AccountSetupConflictError):
        initialize_admin_account(session, account_request())


def test_legacy_shared_password_is_upgraded_on_successful_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "fixed_account_password", "legacy-password")
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    user = User(
        username="legacyadmin",
        name="Legacy Admin",
        email="legacy@example.org",
        role="admin",
        is_active=True,
    )
    session.add(user)
    session.commit()

    account, token = login_account(session, "legacyadmin", "legacy-password")
    monkeypatch.setattr(settings, "fixed_account_password", "")

    assert account.username == "legacyadmin"
    assert user.password_hash is not None
    assert verify_password("legacy-password", user.password_hash)
    assert require_admin(session, token).username == "legacyadmin"
    assert login_account(session, "legacyadmin", "legacy-password")[0].username == "legacyadmin"
    with pytest.raises(AccountLoginError, match="账号或密码错误"):
        login_account(session, "legacyadmin", "wrong-password")


def test_administrator_can_reset_an_account_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    actor, _ = initialize_admin_account(session, account_request())
    actor_user = session.query(User).filter_by(username=actor.username).one()
    create_admin_account(session, account_request("newadmin"))
    session.commit()

    update_admin_account(
        session,
        "newadmin",
        AccountUpdateRequest(password="replacement-password"),
        actor=actor_user,
    )
    session.commit()

    with pytest.raises(AccountLoginError, match="账号或密码错误"):
        login_account(session, "newadmin", "secure-password")
    assert login_account(session, "newadmin", "replacement-password")[0].username == "newadmin"


def test_administrator_creates_account_with_independent_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    actor, _ = initialize_admin_account(session, account_request())
    actor_user = session.query(User).filter_by(username=actor.username).one()
    account = create_admin_account(session, account_request("newadmin"))
    session.commit()

    assert account.is_instance_owner is False
    assert login_account(session, "newadmin", "secure-password")[0].username == "newadmin"
    assert {item.username for item in list_admin_accounts(session)} == {"admin", "newadmin"}
    with pytest.raises(AccountConflictError):
        update_admin_account(
            session,
            actor_user.username or "",
            AccountUpdateRequest(is_active=False),
            actor=actor_user,
        )
    with pytest.raises(HTTPException, match="请先登录"):
        require_admin(session, None)
