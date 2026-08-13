from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, Visibility
from app.domain.models import Activity, Asset, FileRecord, PersonalAccessToken, User
from app.domain.schemas import AccessTokenCreateRequest
from app.main import app
from app.services.access_tokens import AccessTokenConfigurationError, create_access_token
from app.services.security import create_session_token


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def create_user_and_asset(session: Session) -> tuple[User, Asset]:
    user = User(
        username="zhengyu",
        name="郑宇",
        email="zhengyu@sage.lab",
        role="admin",
        is_active=True,
    )
    asset = Asset(
        type=AssetType.LITERATURE,
        slug="agent-upload-paper",
        title="Agent Upload Paper",
        summary="Agent API integration fixture",
        status="published",
        visibility=Visibility.LAB,
        owner=user,
        details={},
    )
    session.add_all([user, asset])
    session.commit()
    return user, asset


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_token(
    session: Session,
    user: User,
    scopes: list[str],
    *,
    name: str = "Codex on MacBook",
) -> str:
    result = create_access_token(
        session,
        user,
        AccessTokenCreateRequest(name=name, scopes=scopes, expires_in_days=30),
    )
    session.commit()
    return result.token


def test_agent_discovery_is_public_and_contains_no_secret() -> None:
    client = TestClient(app)

    instructions = client.get("/agent.md")
    discovery = client.get("/.well-known/datamanager-agent.json")

    assert instructions.status_code == 200
    assert instructions.headers["content-type"].startswith("text/markdown")
    assert "Authorization: Bearer" in instructions.text
    assert "sdm_pat_<public-id>_<secret>" in instructions.text
    assert discovery.json()["openapi"] == "/api/openapi.json"
    openapi = client.get("/api/openapi.json").json()
    get_assets = openapi["paths"]["/api/agent/assets"]["get"]
    assert get_assets["security"] == [{"HTTPBearer": []}]


def test_personal_tokens_are_shown_once_hashed_and_revocable(monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    app.dependency_overrides[get_session] = lambda: session
    headers = {"X-Sage-Session": create_session_token("zhengyu")}
    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/access-tokens",
            headers=headers,
            json={
                "name": "Codex on MacBook",
                "scopes": ["assets:read", "files:upload"],
                "expires_in_days": 30,
            },
        )

        assert response.status_code == 201
        plaintext = response.json()["token"]
        token_id = response.json()["id"]
        assert plaintext.startswith("sdm_pat_")
        record = session.scalar(select(PersonalAccessToken))
        assert record is not None
        assert record.secret_hash not in plaintext
        assert plaintext not in record.secret_hash

        listing = client.get("/api/auth/access-tokens", headers=headers).json()
        assert listing[0]["name"] == "Codex on MacBook"
        assert "token" not in listing[0]

        revoked = client.delete(f"/api/auth/access-tokens/{token_id}", headers=headers)
        assert revoked.status_code == 200
        assert revoked.json()["revoked_at"] is not None
        assert client.get("/api/agent/me", headers=bearer(plaintext)).status_code == 401
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_access_token_name_is_normalized_before_length_validation() -> None:
    payload = AccessTokenCreateRequest(
        name="  Literature sync  ",
        scopes=["assets:read"],
    )
    assert payload.name == "Literature sync"

    for invalid_name in ["  ", " a "]:
        with pytest.raises(ValidationError):
            AccessTokenCreateRequest(name=invalid_name, scopes=["assets:read"])


def test_token_management_is_isolated_to_its_owner(monkeypatch) -> None:
    session = make_session()
    owner, _ = create_user_and_asset(session)
    other = User(
        username="other-admin",
        name="其他管理员",
        email="other@sage.lab",
        role="admin",
        is_active=True,
    )
    session.add(other)
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, owner, ["assets:read"])
    token = session.scalar(select(PersonalAccessToken))
    assert token is not None
    app.dependency_overrides[get_session] = lambda: session
    headers = {"X-Sage-Session": create_session_token("other-admin")}
    try:
        client = TestClient(app)
        assert client.get("/api/auth/access-tokens", headers=headers).json() == []
        revoked = client.delete(f"/api/auth/access-tokens/{token.id}", headers=headers)
        assert revoked.status_code == 404
        assert client.get("/api/agent/me", headers=bearer(plaintext)).status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_token_creation_requires_a_server_signing_secret(monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "")
    monkeypatch.setattr(settings, "fixed_account_password", "")

    try:
        create_access_token(
            session,
            user,
            AccessTokenCreateRequest(
                name="unconfigured",
                scopes=["assets:read"],
                expires_in_days=30,
            ),
        )
        raise AssertionError("token creation should require a signing secret")
    except AccessTokenConfigurationError:
        session.rollback()
    finally:
        session.close()


def test_agent_authentication_persists_last_used_time(monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, user, ["assets:read"])
    token_id = session.scalar(select(PersonalAccessToken.id))
    app.dependency_overrides[get_session] = lambda: session
    try:
        assert TestClient(app).get("/api/agent/me", headers=bearer(plaintext)).status_code == 200
    finally:
        app.dependency_overrides.clear()
        bind = session.get_bind()
        session.close()

    with Session(bind) as verification_session:
        persisted = verification_session.get(PersonalAccessToken, token_id)
        assert persisted is not None
        assert persisted.last_used_at is not None


def test_agent_endpoints_enforce_individual_scopes(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    read_token = create_token(session, user, ["assets:read"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        assert client.get("/api/agent/assets", headers=bearer(read_token)).status_code == 200
        denied = client.post(
            "/api/agent/uploads",
            headers=bearer(read_token),
            json={
                "asset_id": "00000000-0000-0000-0000-000000000001",
                "target_subdirectory": "original",
            },
        )
        assert denied.status_code == 403
        assert denied.json()["detail"] == "访问令牌缺少权限：files:upload"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_missing_scope_does_not_mark_a_token_as_used(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(session, user, ["assets:read"])
    token = session.scalar(select(PersonalAccessToken))
    assert token is not None
    app.dependency_overrides[get_session] = lambda: session
    try:
        denied = TestClient(app).post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={
                "asset_id": "00000000-0000-0000-0000-000000000001",
                "target_subdirectory": "original",
            },
        )
        assert denied.status_code == 403
        session.refresh(token)
        assert token.last_used_at is None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_metadata_creation_records_the_credential_name(monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(
        session,
        user,
        ["metadata:write"],
        name="metadata-curator",
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).post(
            "/api/agent/assets",
            headers=bearer(plaintext),
            json={
                "type": "project",
                "slug": "agent-curated-project",
                "title": "Agent Curated Project",
                "summary": "Created through the scoped Agent API.",
                "status": "active",
                "visibility": "lab",
                "tags": ["agent"],
                "details": {},
                "owner_name": "伪造所有者",
                "owner_email": "spoofed@example.com",
            },
        )
        assert response.status_code == 201
        created_asset = session.scalar(select(Asset).where(Asset.slug == "agent-curated-project"))
        assert created_asset is not None
        assert created_asset.owner_id == user.id
        activity = session.scalars(
            select(Activity).where(Activity.credential_name.is_not(None))
        ).one()
        assert activity.credential_name == "metadata-curator"
        assert activity.actor_id == user.id
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_can_read_and_update_existing_metadata_with_audit_identity(monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(
        session,
        user,
        ["assets:read", "metadata:write"],
        name="metadata-updater",
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        detail = client.get(f"/api/agent/assets/{asset.id}", headers=bearer(plaintext))
        assert detail.status_code == 200
        assert detail.json()["slug"] == asset.slug

        updated = client.patch(
            f"/api/agent/assets/{asset.id}",
            headers=bearer(plaintext),
            json={"summary": "Updated by an authorized metadata agent."},
        )
        assert updated.status_code == 200
        assert updated.json()["summary"] == "Updated by an authorized metadata agent."
        activity = session.scalars(
            select(Activity).where(Activity.action == "updated_metadata")
        ).one()
        assert activity.credential_name == "metadata-updater"
        assert activity.actor_id == user.id
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_read_only_agent_cannot_update_existing_metadata(monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, user, ["assets:read"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).patch(
            f"/api/agent/assets/{asset.id}",
            headers=bearer(plaintext),
            json={"summary": "This update must be rejected."},
        )
        assert response.status_code == 403
        session.refresh(asset)
        assert asset.summary == "Agent API integration fixture"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_expired_token_is_rejected(monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, user, ["assets:read"])
    record = session.scalar(select(PersonalAccessToken))
    assert record is not None
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).get("/api/agent/assets", headers=bearer(plaintext))
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_can_upload_and_finalize_a_file(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(
        session,
        user,
        ["files:upload", "archive:finalize"],
        name="literature-sync",
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        task = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original/official"},
        )
        assert task.status_code == 201
        task_data = task.json()

        upload = client.put(
            f"/api/agent/uploads/{task_data['upload_id']}/files/paper.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task_data["upload_token"],
                "Content-Type": "application/pdf",
            },
            content=b"%PDF-1.7\nagent fixture\n%%EOF",
        )
        assert upload.status_code == 200
        assert upload.json()["relative_path"] == "paper.pdf"
        assert not (tmp_path / ".uploads" / ".parts").exists()

        finalized = client.post(
            task_data["finalize_url"],
            headers=bearer(plaintext),
            json={"upload_token": task_data["upload_token"]},
        )
        assert finalized.status_code == 200
        assert finalized.json()["relative_paths"] == [
            "literature/agent-upload-paper/original/official/paper.pdf"
        ]
        assert (
            tmp_path / "literature/agent-upload-paper/original/official/paper.pdf"
        ).read_bytes().startswith(b"%PDF")
        file_record = session.scalar(select(FileRecord))
        assert file_record is not None
        activities = session.scalars(select(Activity).order_by(Activity.created_at)).all()
        assert activities[-2].credential_name == "literature-sync"
        assert activities[-1].credential_name == "literature-sync"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_rejects_path_escape_and_empty_files(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(session, user, ["files:upload"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        task = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        ).json()
        upload_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task["upload_token"],
        }
        escaped = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/../escape.pdf",
            headers=upload_headers,
            content=b"unsafe",
        )
        empty = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/empty.pdf",
            headers=upload_headers,
            content=b"",
        )
        assert escaped.status_code in {404, 409}
        assert empty.status_code == 409
        assert not (tmp_path / "escape.pdf").exists()
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_rejects_files_over_the_configured_limit(
    tmp_path: Path, monkeypatch
) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "agent_upload_max_bytes", 4)
    plaintext = create_token(session, user, ["files:upload"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        task = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        ).json()
        response = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/oversized.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task["upload_token"],
            },
            content=b"12345",
        )
        assert response.status_code == 413
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_cleans_partial_file_when_stream_exceeds_limit(
    tmp_path: Path, monkeypatch
) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "agent_upload_max_bytes", 4)
    plaintext = create_token(session, user, ["files:upload"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        task = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        ).json()
        response = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/oversized.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task["upload_token"],
                "Transfer-Encoding": "chunked",
            },
            content=(chunk for chunk in [b"123", b"45"]),
        )

        assert response.status_code == 413
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()
