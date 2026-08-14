from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import require_admin
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.models import AccountInvitation, User
from app.domain.schemas import (
    AccountCreateRequest,
    AccountInvitationAcceptRequest,
    AccountInvitationCreateRequest,
    AccountUpdateRequest,
)
from app.main import app
from app.services.accounts import (
    AccountAuthenticationConfigurationError,
    AccountConflictError,
    AccountInvitationInvalidError,
    AccountLoginError,
    AccountSetupConflictError,
    accept_account_invitation,
    create_admin_invitation,
    get_account_invitation,
    initialize_admin_account,
    instance_setup_status,
    list_admin_accounts,
    login_account,
    renew_admin_invitation,
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


def initialize_owner(session: Session) -> User:
    account, _ = initialize_admin_account(session, account_request())
    session.commit()
    return session.scalar(select(User).where(User.id == account.id))


def invitation_token(registration_path: str) -> str:
    return registration_path.rsplit("/", 1)[1]


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
    assert account.is_registered is True
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
        repeated = client.post("/api/auth/setup", json=account_request("secondadmin").model_dump())

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


def test_administrator_reserves_account_and_invitee_completes_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "account_invitation_ttl_seconds", 3600)
    actor = initialize_owner(session)

    created = create_admin_invitation(
        session,
        AccountInvitationCreateRequest(username="newadmin"),
        actor=actor,
    )
    session.commit()
    token = invitation_token(created.registration_path)
    stored = session.scalar(select(AccountInvitation))

    assert len(token) == 64
    assert stored is not None
    assert token not in stored.token_hash
    assert len(stored.token_hash) == 64
    assert created.account.name is None
    assert created.account.email is None
    assert created.account.is_registered is False
    assert get_account_invitation(session, token).username == "newadmin"
    with pytest.raises(AccountLoginError, match="账号不可用"):
        login_account(session, "newadmin", "secure-password")

    account, session_token = accept_account_invitation(
        session,
        token,
        AccountInvitationAcceptRequest(
            name="New Administrator",
            email="newadmin@example.org",
            password="invitee-password",
        ),
    )
    session.commit()

    assert account.name == "New Administrator"
    assert account.email == "newadmin@example.org"
    assert account.is_registered is True
    assert read_session_token(session_token) == "newadmin"
    assert login_account(session, "newadmin", "invitee-password")[0].username == "newadmin"
    with pytest.raises(AccountInvitationInvalidError):
        get_account_invitation(session, token)
    with pytest.raises(AccountInvitationInvalidError):
        accept_account_invitation(
            session,
            token,
            AccountInvitationAcceptRequest(password="another-password"),
        )


def test_reissuing_registration_link_revokes_the_previous_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    actor = initialize_owner(session)
    first = create_admin_invitation(
        session, AccountInvitationCreateRequest(username="newadmin"), actor=actor
    )
    session.commit()

    second = renew_admin_invitation(session, "newadmin", actor=actor, purpose="registration")
    session.commit()

    with pytest.raises(AccountInvitationInvalidError):
        get_account_invitation(session, invitation_token(first.registration_path))
    assert (
        get_account_invitation(session, invitation_token(second.registration_path)).purpose
        == "registration"
    )


def test_password_recovery_link_lets_account_owner_choose_the_new_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    actor = initialize_owner(session)
    recovery = renew_admin_invitation(session, "admin", actor=actor, purpose="recovery")
    session.commit()

    account, _ = accept_account_invitation(
        session,
        invitation_token(recovery.registration_path),
        AccountInvitationAcceptRequest(password="replacement-password"),
    )
    session.commit()

    assert account.name == "Instance Administrator"
    with pytest.raises(AccountLoginError, match="账号或密码错误"):
        login_account(session, "admin", "secure-password")
    assert login_account(session, "admin", "replacement-password")[0].username == "admin"


def test_invitation_api_is_public_but_creation_requires_an_administrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    actor = initialize_owner(session)
    _, session_token = login_account(session, actor.username or "", "secure-password")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        assert (
            client.post("/api/auth/admin-accounts", json={"username": "invited"}).status_code == 401
        )
        created = client.post(
            "/api/auth/admin-accounts",
            json={"username": "invited"},
            headers={"X-Sage-Session": session_token},
        )
        token = invitation_token(created.json()["registration_path"])
        status_response = client.get(f"/api/auth/invitations/{token}")
        accepted = client.post(
            f"/api/auth/invitations/{token}/accept",
            json={
                "name": "Invited Admin",
                "email": "invited@example.org",
                "password": "invitee-password",
            },
        )

        assert created.status_code == 201
        assert len(token) == 64
        assert status_response.status_code == 200
        assert status_response.json()["purpose"] == "registration"
        assert accepted.status_code == 200
        assert accepted.json()["username"] == "invited"
        assert client.get(f"/api/auth/invitations/{token}").status_code == 404
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_registered_account_can_be_disabled_but_pending_profile_cannot_be_edited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    actor = initialize_owner(session)
    invitation = create_admin_invitation(
        session, AccountInvitationCreateRequest(username="newadmin"), actor=actor
    )
    session.commit()
    pending = session.scalar(select(User).where(User.username == "newadmin"))
    assert pending is not None

    with pytest.raises(AccountConflictError, match="受邀者填写"):
        update_admin_account(
            session,
            "newadmin",
            AccountUpdateRequest(name="Admin Filled Name"),
            actor=actor,
        )

    accept_account_invitation(
        session,
        invitation_token(invitation.registration_path),
        AccountInvitationAcceptRequest(
            name="New Admin",
            email="newadmin@example.org",
            password="invitee-password",
        ),
    )
    update_admin_account(
        session,
        "newadmin",
        AccountUpdateRequest(is_active=False),
        actor=actor,
    )
    session.commit()

    summaries = {item.username: item for item in list_admin_accounts(session)}
    assert summaries["newadmin"].is_active is False
    with pytest.raises(AccountConflictError, match="不能停用"):
        update_admin_account(
            session,
            actor.username or "",
            AccountUpdateRequest(is_active=False),
            actor=actor,
        )
    with pytest.raises(HTTPException, match="请先登录"):
        require_admin(session, None)


def test_invitation_cannot_be_accepted_without_a_session_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "fixed_account_password", "")
    actor = initialize_owner(session)
    created = create_admin_invitation(
        session, AccountInvitationCreateRequest(username="newadmin"), actor=actor
    )
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "")

    with pytest.raises(AccountAuthenticationConfigurationError, match="SAGE_AUTH_SESSION_SECRET"):
        accept_account_invitation(
            session,
            invitation_token(created.registration_path),
            AccountInvitationAcceptRequest(
                name="New Admin",
                email="newadmin@example.org",
                password="invitee-password",
            ),
        )


def test_expired_invitation_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    actor = initialize_owner(session)
    created = create_admin_invitation(
        session, AccountInvitationCreateRequest(username="newadmin"), actor=actor
    )
    invitation = session.scalar(select(AccountInvitation))
    assert invitation is not None
    invitation.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    session.commit()

    with pytest.raises(AccountInvitationInvalidError):
        get_account_invitation(session, invitation_token(created.registration_path))
