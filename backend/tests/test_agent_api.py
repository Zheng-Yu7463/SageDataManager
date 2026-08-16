import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.access_tokens as access_token_service
import app.services.transfers as transfer_service
from app.api.routes import agent as agent_routes
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, HealthStatus, Visibility
from app.domain.models import (
    Activity,
    Asset,
    FileRecord,
    PersonalAccessToken,
    UploadTask,
    User,
)
from app.domain.schemas import AccessTokenCreateRequest
from app.main import AGENT_DOCUMENT_VERSION, AGENT_PROTOCOL_VERSION, app
from app.services.access_tokens import AccessTokenConfigurationError, create_access_token
from app.services.security import create_session_token
from app.services.storage import UPLOAD_LOCKS_DIRECTORY


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
    assert instructions.headers["cache-control"] == "no-cache"
    assert discovery.headers["cache-control"] == "no-cache"
    assert f"Protocol version: {AGENT_PROTOCOL_VERSION}" in instructions.text
    assert f"Document version: {AGENT_DOCUMENT_VERSION}" in instructions.text
    assert "{{PROTOCOL_VERSION}}" not in instructions.text
    assert "{{DOCUMENT_VERSION}}" not in instructions.text
    assert "{{MAXIMUM_FILE_SIZE_BYTES}}" not in instructions.text
    assert "Authorization: Bearer" in instructions.text
    assert "sdm_pat_<public-id>_<secret>" in instructions.text
    assert "Authority and confirmation boundaries" in instructions.text
    assert "Search and duplicate prevention" in instructions.text
    assert "End-to-end examples" in instructions.text
    assert "machine-readable contract" in instructions.text
    assert "PUT, finalize, or cancel return `507 upload_storage_unavailable`" in instructions.text
    assert "agent_scope_missing" in instructions.text
    assert "Never blindly overwrite concurrent work" in instructions.text
    assert 'target_subdirectory:"documentation"' in instructions.text
    assert 'target_subdirectory:"original"' not in instructions.text
    assert "$UPLOAD_ID?include_checksums=true" in instructions.text
    assert ".relative_paths == [$path]" in instructions.text
    assert "SAGE_TOKEN=" not in instructions.text
    discovery_data = discovery.json()
    assert discovery_data["openapi"] == "/api/openapi.json"
    assert discovery_data["schema_version"] == AGENT_PROTOCOL_VERSION
    assert discovery_data["documentation_version"] == AGENT_DOCUMENT_VERSION
    assert discovery_data["errors"] == {
        "code_header": "X-Sage-Error-Code",
        "codes": [
            "agent_auth_required",
            "agent_auth_invalid",
            "agent_auth_unavailable",
            "agent_scope_missing",
            "asset_not_found",
            "asset_slug_conflict",
            "asset_metadata_conflict",
            "asset_revision_conflict",
            "file_not_found",
            "file_preview_unavailable",
            "file_unavailable",
            "citation_incomplete",
            "request_invalid",
            "range_invalid",
            "range_not_satisfiable",
        ],
    }
    assert "file_read" in discovery_data["capabilities"]
    assert "upload_manifest_summary" in discovery_data["capabilities"]
    assert discovery_data["scopes"]["files:read"] == ["GET /files/{file_id}/content"]
    assert discovery_data["limits"]["maximum_file_size_bytes"] == 500_000_000
    assert discovery_data["limits"]["maximum_upload_files_per_task"] == 10_000
    assert discovery_data["limits"]["maximum_upload_total_bytes"] == 50_000_000_000
    assert discovery_data["limits"]["maximum_publication_author_characters"] == 200
    assert discovery_data["limits"]["maximum_asset_details_bytes"] == 256_000
    assert discovery_data["limits"]["maximum_upload_path_characters"] == 1000
    assert discovery_data["limits"]["maximum_upload_path_bytes"] == 1000
    assert discovery_data["limits"]["maximum_upload_path_component_characters"] == 255
    assert discovery_data["limits"]["maximum_upload_path_component_bytes"] == 255
    assert discovery_data["uploads"] == {
        "task_token_header": "X-Sage-Upload-Token",
        "checksum_header": "X-Sage-Content-SHA256",
        "error_code_header": "X-Sage-Error-Code",
        "retry_after_header": "Retry-After",
        "status_checksum_query_parameter": "include_checksums",
        "manifest_summary_fields": ["expected_file_count", "expected_total_size"],
        "manifest_summary_required_for_new_clients": True,
        "error_codes": [
            "invalid_checksum",
            "invalid_content_length",
            "upload_busy",
            "upload_cancel_failed",
            "upload_conflict",
            "upload_credentials_invalid",
            "upload_invalid",
            "upload_manifest_mismatch",
            "upload_manifest_too_large",
            "upload_not_ready",
            "upload_status_unavailable",
            "upload_storage_unavailable",
            "upload_task_too_large",
            "upload_target_invalid",
            "upload_token_missing",
            "upload_too_large",
        ],
        "status_values": ["waiting", "ready", "completed", "cancelled"],
        "empty_files_allowed": False,
        "file_put_idempotency": {
            "requires_checksum": True,
            "match_fields": ["relative_path", "checksum_sha256"],
            "content_length_must_match_when_present": True,
            "overwrites_existing_file": False,
        },
    }
    assert str(discovery_data["limits"]["maximum_file_size_bytes"]) in instructions.text
    assert str(discovery_data["limits"]["maximum_upload_files_per_task"]) in instructions.text
    assert str(discovery_data["limits"]["maximum_upload_total_bytes"]) in instructions.text
    assert discovery_data["asset_types"] == [asset_type.value for asset_type in AssetType]
    for asset_type, directories in discovery_data["upload_directories"].items():
        assert asset_type in instructions.text
        assert all(directory in instructions.text for directory in directories)
    assert "X-Sage-Asset-Revision" in instructions.text
    assert "archive:finalize" in instructions.text
    openapi = client.get("/api/openapi.json").json()
    documented_operations = {
        ("get", "/api/agent/me"),
        ("get", "/api/agent/assets"),
        ("post", "/api/agent/assets"),
        ("get", "/api/agent/assets/{asset_id}"),
        ("patch", "/api/agent/assets/{asset_id}"),
        ("get", "/api/agent/files/{file_id}/content"),
        ("get", "/api/agent/assets/{asset_id}/citation/bibtex"),
        ("post", "/api/agent/uploads"),
        ("get", "/api/agent/uploads/{upload_id}"),
        ("delete", "/api/agent/uploads/{upload_id}"),
        ("put", "/api/agent/uploads/{upload_id}/files/{relative_path}"),
        ("post", "/api/agent/uploads/{upload_id}/finalize"),
    }
    for method, path in documented_operations:
        assert method in openapi["paths"][path]
    get_assets = openapi["paths"]["/api/agent/assets"]["get"]
    assert get_assets["security"] == [{"HTTPBearer": []}]
    file_content = openapi["paths"]["/api/agent/files/{file_id}/content"]["get"]
    assert file_content["summary"] == "Read or download an indexed file"
    patch_asset = openapi["paths"]["/api/agent/assets/{asset_id}"]["patch"]
    revision_header = next(
        parameter
        for parameter in patch_asset["parameters"]
        if parameter["name"] == "X-Sage-Asset-Revision"
    )
    assert revision_header["required"] is True
    assert "409" in patch_asset["responses"]
    upload_file = openapi["paths"]["/api/agent/uploads/{upload_id}/files/{relative_path}"]["put"]
    assert {"400", "401", "403", "409", "413", "422"} <= set(upload_file["responses"])
    assert "same path, size, and SHA-256" in upload_file["description"]


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


def test_access_token_finalize_scope_requires_upload_scope() -> None:
    with pytest.raises(ValidationError, match="正式入库权限需要同时授予上传文件权限"):
        AccessTokenCreateRequest(
            name="invalid-finalizer",
            scopes=["archive:finalize"],
        )


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


def test_well_formed_unknown_token_uses_the_hmac_path(monkeypatch) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    comparisons: list[tuple[str, str]] = []
    original_compare = access_token_service.hmac.compare_digest

    def record_compare(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(access_token_service.hmac, "compare_digest", record_compare)

    try:
        result = access_token_service.authenticate_access_token(
            session,
            "sdm_pat_unknown_public_id_unknown-secret",
        )
    finally:
        session.close()

    assert result is None
    assert len(comparisons) == 1
    assert all(len(value) == 64 for value in comparisons[0])


def test_unknown_token_reports_missing_signing_configuration(monkeypatch) -> None:
    session = make_session()
    monkeypatch.setattr(settings, "auth_session_secret", "")
    monkeypatch.setattr(settings, "fixed_account_password", "")
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).get(
            "/api/agent/me",
            headers=bearer("sdm_pat_unknown_public_id_unknown-secret"),
        )
    finally:
        app.dependency_overrides.clear()
        session.close()

    assert response.status_code == 503
    assert response.headers["x-sage-error-code"] == "agent_auth_unavailable"


def test_agent_authentication_persists_last_used_time(monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, user, ["assets:read"])
    token_id = session.scalar(select(PersonalAccessToken.id))
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        assert client.get("/api/agent/me", headers=bearer(plaintext)).status_code == 200
        token = session.get(PersonalAccessToken, token_id)
        assert token is not None
        first_used_at = token.last_used_at

        assert client.get("/api/agent/me", headers=bearer(plaintext)).status_code == 200
        session.refresh(token)
        assert token.last_used_at == first_used_at
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
        assert denied.headers["x-sage-error-code"] == "agent_scope_missing"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_domain_errors_expose_stable_codes(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(
        session,
        user,
        [
            "assets:read",
            "files:read",
            "metadata:write",
            "files:upload",
            "archive:finalize",
            "citations:export",
        ],
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        missing_auth = client.get("/api/agent/assets")
        missing_asset = client.get(
            f"/api/agent/assets/{uuid4()}",
            headers=bearer(plaintext),
        )
        missing_file = client.get(
            f"/api/agent/files/{uuid4()}/content",
            headers=bearer(plaintext),
        )
        incomplete_citation = client.get(
            f"/api/agent/assets/{asset.id}/citation/bibtex",
            headers=bearer(plaintext),
        )
        duplicate_slug = client.post(
            "/api/agent/assets",
            headers=bearer(plaintext),
            json={
                "type": "project",
                "slug": asset.slug,
                "title": "Conflicting slug",
                "status": "active",
                "visibility": "lab",
            },
        )

        assert missing_auth.status_code == 401
        assert missing_auth.headers["x-sage-error-code"] == "agent_auth_required"
        assert missing_asset.status_code == 404
        assert missing_asset.headers["x-sage-error-code"] == "asset_not_found"
        assert missing_file.status_code == 404
        assert missing_file.headers["x-sage-error-code"] == "file_not_found"
        assert incomplete_citation.status_code == 409
        assert incomplete_citation.headers["x-sage-error-code"] == "citation_incomplete"
        assert duplicate_slug.status_code == 409
        assert duplicate_slug.headers["x-sage-error-code"] == "asset_slug_conflict"
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


def test_agent_rejects_oversized_publication_author_name(monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, user, ["metadata:write"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).post(
            "/api/agent/assets",
            headers=bearer(plaintext),
            json={
                "type": "paper",
                "slug": "oversized-author-paper",
                "title": "Oversized Author Paper",
                "status": "published",
                "details": {
                    "venue": "ACL",
                    "year": 2026,
                    "track": "Main Conference",
                    "authors": ["A" * 201],
                    "source_id": "2026.acl-long.999",
                    "source_url": "https://example.com/source",
                    "pdf_url": "https://example.com/paper.pdf",
                },
            },
        )

        assert response.status_code == 422
        assert response.headers["x-sage-error-code"] == "request_invalid"
        assert session.scalar(
            select(Asset).where(Asset.slug == "oversized-author-paper")
        ) is None
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_rejects_oversized_asset_details(monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, user, ["metadata:write"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).post(
            "/api/agent/assets",
            headers=bearer(plaintext),
            json={
                "type": "project",
                "slug": "oversized-details-project",
                "title": "Oversized Details Project",
                "details": {"generated_output": "x" * 256_000},
            },
        )

        assert response.status_code == 422
        assert response.headers["x-sage-error-code"] == "request_invalid"
        assert session.scalar(
            select(Asset).where(Asset.slug == "oversized-details-project")
        ) is None
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
        original_revision = detail.json()["updated_at"]

        updated = client.patch(
            f"/api/agent/assets/{asset.id}",
            headers={
                **bearer(plaintext),
                "X-Sage-Asset-Revision": original_revision,
            },
            json={"summary": "Updated by an authorized metadata agent."},
        )
        assert updated.status_code == 200
        assert updated.json()["summary"] == "Updated by an authorized metadata agent."
        updated_at = session.get(Asset, asset.id).updated_at
        replayed = client.patch(
            f"/api/agent/assets/{asset.id}",
            headers={
                **bearer(plaintext),
                "X-Sage-Asset-Revision": updated.json()["updated_at"],
            },
            json={"summary": "Updated by an authorized metadata agent."},
        )
        assert replayed.status_code == 200
        assert session.get(Asset, asset.id).updated_at == updated_at
        stale = client.patch(
            f"/api/agent/assets/{asset.id}",
            headers={
                **bearer(plaintext),
                "X-Sage-Asset-Revision": original_revision,
            },
            json={"summary": "Stale overwrite"},
        )
        assert stale.status_code == 409
        assert stale.headers["x-sage-error-code"] == "asset_revision_conflict"
        assert session.get(Asset, asset.id).summary == ("Updated by an authorized metadata agent.")
        activities = session.scalars(
            select(Activity).where(Activity.action == "updated_metadata")
        ).all()
        assert len(activities) == 1
        activity = activities[0]
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
            headers={
                **bearer(plaintext),
                "X-Sage-Asset-Revision": datetime.now(UTC).isoformat(),
            },
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
        assert response.headers["x-sage-error-code"] == "agent_auth_invalid"
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
            json={
                "asset_id": str(asset.id),
                "target_subdirectory": "original/official",
                "expected_file_count": 1,
                "expected_total_size": len(b"%PDF-1.7\nagent fixture\n%%EOF"),
            },
        )
        assert task.status_code == 201
        task_data = task.json()
        assert task_data["expected_file_count"] == 1
        assert task_data["expected_total_size"] == len(
            b"%PDF-1.7\nagent fixture\n%%EOF"
        )

        not_ready = client.post(
            task_data["finalize_url"],
            headers=bearer(plaintext),
            json={"upload_token": task_data["upload_token"]},
        )
        assert not_ready.status_code == 409
        assert not_ready.headers["x-sage-error-code"] == "upload_not_ready"

        content = b"%PDF-1.7\nagent fixture\n%%EOF"
        expected_checksum = hashlib.sha256(content).hexdigest()
        upload = client.put(
            f"/api/agent/uploads/{task_data['upload_id']}/files/paper.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task_data["upload_token"],
                "X-Sage-Content-SHA256": expected_checksum,
                "Content-Type": "application/pdf",
            },
            content=content,
        )
        assert upload.status_code == 200
        assert upload.json()["relative_path"] == "paper.pdf"
        assert upload.json()["checksum_sha256"] == expected_checksum
        assert not (tmp_path / ".uploads" / ".parts").exists()

        replayed_upload = client.put(
            f"/api/agent/uploads/{task_data['upload_id']}/files/paper.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task_data["upload_token"],
                "X-Sage-Content-SHA256": expected_checksum,
                "Content-Type": "application/pdf",
            },
            content=content,
        )
        assert replayed_upload.status_code == 200
        assert replayed_upload.json() == upload.json()

        changed_content = b"x" * len(content)
        conflicting_replay = client.put(
            f"/api/agent/uploads/{task_data['upload_id']}/files/paper.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task_data["upload_token"],
                "X-Sage-Content-SHA256": hashlib.sha256(changed_content).hexdigest(),
            },
            content=changed_content,
        )
        unverifiable_replay = client.put(
            f"/api/agent/uploads/{task_data['upload_id']}/files/paper.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task_data["upload_token"],
            },
            content=content,
        )
        assert conflicting_replay.status_code == 409
        assert conflicting_replay.headers["x-sage-error-code"] == "upload_conflict"
        assert unverifiable_replay.status_code == 409
        assert unverifiable_replay.headers["x-sage-error-code"] == "upload_conflict"

        finalized = client.post(
            task_data["finalize_url"],
            headers=bearer(plaintext),
            json={"upload_token": task_data["upload_token"]},
        )
        assert finalized.status_code == 200
        assert finalized.json()["relative_paths"] == [
            "literature/agent-upload-paper/original/official/paper.pdf"
        ]
        assert list(finalized.json()["checksums"].values()) == [expected_checksum]
        task_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task_data["upload_token"],
        }
        completed_status = client.get(task_data["status_url"], headers=task_headers)
        rejected_cancel = client.delete(task_data["cancel_url"], headers=task_headers)
        assert completed_status.status_code == 200
        assert completed_status.json()["status"] == "completed"
        assert completed_status.json()["asset_id"] == task_data["asset_id"]
        assert completed_status.json()["expected_file_count"] == 1
        assert completed_status.json()["expected_total_size"] == len(content)
        assert (
            completed_status.json()["archive_relative_path"] == (task_data["archive_relative_path"])
        )
        assert completed_status.json()["result"] == finalized.json()
        assert rejected_cancel.status_code == 409
        replayed = client.post(
            task_data["finalize_url"],
            headers=bearer(plaintext),
            json={"upload_token": task_data["upload_token"]},
        )
        rejected_upload = client.put(
            f"/api/agent/uploads/{task_data['upload_id']}/files/late.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task_data["upload_token"],
            },
            content=b"late content",
        )
        assert replayed.status_code == 200
        assert replayed.json() == finalized.json()
        assert rejected_upload.status_code == 403
        assert (
            (tmp_path / "literature/agent-upload-paper/original/official/paper.pdf")
            .read_bytes()
            .startswith(b"%PDF")
        )
        file_record = session.scalar(select(FileRecord))
        assert file_record is not None
        assert file_record.checksum == expected_checksum
        activities = session.scalars(select(Activity).order_by(Activity.created_at)).all()
        assert activities[-2].credential_name == "literature-sync"
        assert activities[-1].credential_name == "literature-sync"
        assert sum(activity.action == "completed_upload" for activity in activities) == 1
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_enforces_declared_manifest(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(
        session, user, ["files:upload", "archive:finalize"], name="bounded-uploader"
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        partial_manifest = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={
                "asset_id": str(asset.id),
                "target_subdirectory": "original",
                "expected_file_count": 2,
            },
        )
        assert partial_manifest.status_code == 422
        assert partial_manifest.headers["x-sage-error-code"] == "request_invalid"

        task_response = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={
                "asset_id": str(asset.id),
                "target_subdirectory": "original",
                "expected_file_count": 2,
                "expected_total_size": 5,
            },
        )
        assert task_response.status_code == 201
        task = task_response.json()
        assert task["expected_file_count"] == 2
        assert task["expected_total_size"] == 5
        task_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task["upload_token"],
        }

        first = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/first.bin",
            headers=task_headers,
            content=b"abc",
        )
        assert first.status_code == 200
        waiting = client.get(task["status_url"], headers=task_headers)
        assert waiting.status_code == 200
        assert waiting.json()["status"] == "waiting"
        assert waiting.json()["uploaded_file_count"] == 1
        assert waiting.json()["total_size"] == 3
        assert waiting.json()["expected_file_count"] == 2
        assert waiting.json()["expected_total_size"] == 5

        early_finalize = client.post(
            task["finalize_url"],
            headers=bearer(plaintext),
            json={"upload_token": task["upload_token"]},
        )
        assert early_finalize.status_code == 409
        assert early_finalize.headers["x-sage-error-code"] == "upload_manifest_mismatch"

        overflow = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/second.bin",
            headers=task_headers,
            content=b"def",
        )
        assert overflow.status_code == 409
        assert overflow.headers["x-sage-error-code"] == "upload_manifest_mismatch"
        assert not (
            tmp_path / ".uploads" / str(task["upload_id"]) / "second.bin"
        ).exists()

        second = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/second.bin",
            headers=task_headers,
            content=b"de",
        )
        assert second.status_code == 200
        ready = client.get(task["status_url"], headers=task_headers)
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["uploaded_file_count"] == 2
        assert ready.json()["total_size"] == 5

        extra = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/third.bin",
            headers=task_headers,
            content=b"x",
        )
        assert extra.status_code == 409
        assert extra.headers["x-sage-error-code"] == "upload_manifest_mismatch"

        finalized = client.post(
            task["finalize_url"],
            headers=bearer(plaintext),
            json={"upload_token": task["upload_token"]},
        )
        assert finalized.status_code == 200
        assert finalized.json()["imported_file_count"] == 2
        assert finalized.json()["total_size"] == 5

        monkeypatch.setattr(settings, "agent_upload_max_files_per_task", 1)
        oversized_manifest = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={
                "asset_id": str(asset.id),
                "target_subdirectory": "original",
                "expected_file_count": 2,
                "expected_total_size": 2,
            },
        )
        assert oversized_manifest.status_code == 413
        assert (
            oversized_manifest.headers["x-sage-error-code"]
            == "upload_manifest_too_large"
        )
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_invalid_agent_upload_task_does_not_create_a_lock_file(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, _ = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(session, user, ["files:upload", "archive:finalize"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        upload_id = uuid4()
        task_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": "invalid-upload-token",
        }

        cancelled = client.delete(f"/api/agent/uploads/{upload_id}", headers=task_headers)
        finalized = client.post(
            f"/api/agent/uploads/{upload_id}/finalize",
            headers=bearer(plaintext),
            json={"upload_token": "invalid-upload-token"},
        )

        assert cancelled.status_code == 403
        assert cancelled.headers["x-sage-error-code"] == "upload_credentials_invalid"
        assert finalized.status_code == 403
        assert finalized.headers["x-sage-error-code"] == "upload_credentials_invalid"
        assert not (tmp_path / UPLOAD_LOCKS_DIRECTORY).exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_returns_conflict_while_the_task_is_busy(tmp_path: Path, monkeypatch) -> None:
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
        upload_url = f"/api/agent/uploads/{task['upload_id']}/files/paper.pdf"
        upload_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task["upload_token"],
        }

        with transfer_service.upload_task_guard(tmp_path, UUID(task["upload_id"])):
            busy = client.put(upload_url, headers=upload_headers, content=b"content")

        assert busy.status_code == 409
        assert busy.json()["detail"] == "上传任务正在处理，请检查任务状态后重试。"
        assert busy.headers["x-sage-error-code"] == "upload_busy"
        assert busy.headers["retry-after"] == "1"
        assert not (tmp_path / ".uploads").exists()

        retried = client.put(upload_url, headers=upload_headers, content=b"content")
        assert retried.status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_rejects_mismatched_declared_checksum(tmp_path: Path, monkeypatch) -> None:
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

        response = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/paper.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task["upload_token"],
                "X-Sage-Content-SHA256": "0" * 64,
            },
            content=b"actual content",
        )

        assert response.status_code == 409
        assert "SHA-256 校验失败" in response.json()["detail"]
        assert response.headers["x-sage-error-code"] == "upload_invalid"
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_task_is_bound_to_the_creating_access_token(
    tmp_path: Path, monkeypatch
) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    first_token = create_token(session, user, ["files:upload"], name="first-agent")
    second_token = create_token(session, user, ["files:upload"], name="second-agent")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        task = client.post(
            "/api/agent/uploads",
            headers=bearer(first_token),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        ).json()

        response = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/paper.pdf",
            headers={
                **bearer(second_token),
                "X-Sage-Upload-Token": task["upload_token"],
            },
            content=b"content",
        )

        assert response.status_code == 403
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_reports_an_unavailable_storage_root(tmp_path: Path, monkeypatch) -> None:
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
        missing_root = tmp_path / "missing-storage"
        monkeypatch.setattr(settings, "storage_root", missing_root)

        response = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/paper.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task["upload_token"],
            },
            content=b"content",
        )

        assert response.status_code == 409
        assert "存储根不可用" in response.json()["detail"]
        assert response.headers["x-sage-error-code"] == "upload_invalid"
        assert not missing_root.exists()
        assert not (tmp_path / ".uploads").exists()
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
        reserved = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/notes/.sage-upload-complete",
            headers=upload_headers,
            content=b"reserved",
        )
        oversized_name = "文" * 86
        oversized_component = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/{oversized_name}",
            headers=upload_headers,
            content=b"too long for the filesystem",
        )
        empty = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/empty.pdf",
            headers=upload_headers,
            content=b"",
        )
        assert escaped.status_code in {404, 409}
        assert reserved.status_code == 409
        assert "系统保留名称" in reserved.json()["detail"]
        assert oversized_component.status_code == 409
        assert oversized_component.headers["x-sage-error-code"] == "upload_invalid"
        assert empty.status_code == 409
        assert not (tmp_path / "escape.pdf").exists()
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_rejects_symlinked_parts_directory(
    tmp_path: Path, monkeypatch
) -> None:
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
        external = tmp_path / "outside-parts"
        external.mkdir()
        staging_root = tmp_path / ".uploads"
        staging_root.mkdir()
        (staging_root / ".parts").symlink_to(external, target_is_directory=True)

        response = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/paper.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task["upload_token"],
            },
            content=b"must stay inside storage",
        )

        assert response.status_code == 409
        assert response.headers["x-sage-error-code"] == "upload_invalid"
        assert "分片临时区不可用" in response.json()["detail"]
        assert list(external.iterdir()) == []
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_rejects_files_over_the_configured_limit(tmp_path: Path, monkeypatch) -> None:
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
        invalid_length = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/invalid-length.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task["upload_token"],
                "Content-Length": "-1",
            },
            content=b"",
        )
        assert invalid_length.status_code == 400
        assert invalid_length.headers["x-sage-error-code"] == "invalid_content_length"

        response = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/oversized.pdf",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": task["upload_token"],
            },
            content=b"12345",
        )
        assert response.status_code == 413
        assert response.headers["x-sage-error-code"] == "upload_too_large"
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_legacy_agent_upload_still_enforces_instance_task_limits(
    tmp_path: Path, monkeypatch
) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "agent_upload_max_files_per_task", 1)
    monkeypatch.setattr(settings, "agent_upload_max_total_bytes", 10)
    plaintext = create_token(session, user, ["files:upload"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        first_task = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        ).json()
        first_upload_id = first_task["upload_id"]
        first_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": first_task["upload_token"],
        }
        assert client.put(
            f"/api/agent/uploads/{first_upload_id}/files/first.bin",
            headers=first_headers,
            content=b"1234",
        ).status_code == 200
        extra_file = client.put(
            f"/api/agent/uploads/{first_upload_id}/files/second.bin",
            headers=first_headers,
            content=b"x",
        )
        assert extra_file.status_code == 413
        assert extra_file.headers["x-sage-error-code"] == "upload_task_too_large"

        monkeypatch.setattr(settings, "agent_upload_max_files_per_task", 2)
        monkeypatch.setattr(settings, "agent_upload_max_total_bytes", 4)
        second_task = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        ).json()
        second_upload_id = second_task["upload_id"]
        oversized_task = client.put(
            f"/api/agent/uploads/{second_upload_id}/files/oversized.bin",
            headers={
                **bearer(plaintext),
                "X-Sage-Upload-Token": second_task["upload_token"],
            },
            content=b"12345",
        )
        assert oversized_task.status_code == 413
        assert (
            oversized_task.headers["x-sage-error-code"] == "upload_task_too_large"
        )
        assert not (
            tmp_path / ".uploads" / second_upload_id / "oversized.bin"
        ).exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_finalize_and_cancel_report_task_storage_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(
        session,
        user,
        ["files:upload", "archive:finalize"],
        name="storage-failure-token",
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        task = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        ).json()
        task_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task["upload_token"],
        }

        def reject_storage(*_args: object, **_kwargs: object):
            raise transfer_service.UploadStorageError("internal storage detail")

        monkeypatch.setattr(agent_routes, "finalize_upload", reject_storage)
        finalized = client.post(
            task["finalize_url"],
            headers=bearer(plaintext),
            json={"upload_token": task["upload_token"]},
        )
        monkeypatch.setattr(agent_routes, "cancel_agent_upload", reject_storage)
        cancelled = client.delete(task["cancel_url"], headers=task_headers)

        for response in (finalized, cancelled):
            assert response.status_code == 507
            assert response.headers["x-sage-error-code"] == "upload_storage_unavailable"
            assert "internal storage detail" not in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_reports_storage_sync_failure_and_cleans_partial_file(
    tmp_path: Path, monkeypatch
) -> None:
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

        def reject_sync(descriptor: int) -> None:
            raise OSError("simulated full disk")

        monkeypatch.setattr("app.api.routes.agent.os.fsync", reject_sync)
        task_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task["upload_token"],
        }
        response = client.put(
            f"/api/agent/uploads/{task['upload_id']}/files/paper.pdf",
            headers=task_headers,
            content=b"content that cannot be synced",
        )

        assert response.status_code == 507
        assert response.headers["x-sage-error-code"] == "upload_storage_unavailable"
        assert not (tmp_path / ".uploads").exists()
        status_response = client.get(task["status_url"], headers=task_headers)
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "waiting"
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
        assert response.headers["x-sage-error-code"] == "upload_too_large"
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_asset_list_is_compact_and_uses_ai_friendly_default_page_size(
    monkeypatch,
) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    asset.details = {"source_id": "fixture-source", "large": "x" * 10_000}
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, user, ["assets:read"])
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).get("/api/agent/assets", headers=bearer(plaintext))

        assert response.status_code == 200
        payload = response.json()
        assert payload["page_size"] == 10
        assert payload["total"] == 1
        item = payload["items"][0]
        assert item["source_id"] == "fixture-source"
        assert item["file_count"] == 0
        assert "summary" not in item
        assert "details" not in item
        assert "owner" not in item
        assert "upload_directories" not in item
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_metadata_update_requires_revision_header(monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    plaintext = create_token(session, user, ["metadata:write"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).patch(
            f"/api/agent/assets/{asset.id}",
            headers=bearer(plaintext),
            json={"summary": "Must not be accepted without a revision."},
        )

        assert response.status_code == 422
        assert response.headers["x-sage-error-code"] == "request_invalid"
        session.refresh(asset)
        assert asset.summary == "Agent API integration fixture"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_access_token_accepts_the_complete_scope_set() -> None:
    payload = AccessTokenCreateRequest(
        name="full-access-agent",
        scopes=[
            "assets:read",
            "files:read",
            "metadata:write",
            "files:upload",
            "archive:finalize",
            "citations:export",
        ],
    )

    assert len(payload.scopes) == 6


def test_agent_can_range_read_an_indexed_file_with_pat_audit(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    archived_file = tmp_path / "literature" / asset.slug / "original" / "agent-fixture.txt"
    archived_file.parent.mkdir(parents=True)
    content = b"0123456789"
    archived_file.write_bytes(content)
    metadata = archived_file.stat()
    record = FileRecord(
        asset=asset,
        relative_path=archived_file.relative_to(tmp_path).as_posix(),
        file_name=archived_file.name,
        file_kind="document",
        mime_type="text/plain",
        file_size=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
        health_status=HealthStatus.HEALTHY,
        modified_at=datetime.fromtimestamp(metadata.st_mtime, UTC),
    )
    session.add(record)
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(
        session,
        user,
        ["files:read"],
        name="file-reader",
    )
    read_only_metadata_token = create_token(session, user, ["assets:read"])
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        denied = client.get(
            f"/api/agent/files/{record.id}/content",
            headers=bearer(read_only_metadata_token),
        )
        head_response = client.head(
            f"/api/agent/files/{record.id}/content",
            headers=bearer(plaintext),
        )
        assert head_response.status_code == 200
        assert head_response.content == b""
        assert (
            session.scalars(select(Activity).where(Activity.action == "downloaded_file")).all()
            == []
        )
        response = client.get(
            f"/api/agent/files/{record.id}/content",
            headers={**bearer(plaintext), "Range": "bytes=2-5"},
        )

        assert denied.status_code == 403
        assert denied.headers["x-sage-error-code"] == "agent_scope_missing"
        assert response.status_code == 206
        assert response.content == b"2345"
        assert response.headers["content-range"] == "bytes 2-5/10"
        assert response.headers["cache-control"] == "private, no-store"
        invalid_range = client.get(
            f"/api/agent/files/{record.id}/content",
            headers={**bearer(plaintext), "Range": "bytes=20-"},
        )
        assert invalid_range.status_code == 416
        assert invalid_range.headers["x-sage-error-code"] == "range_not_satisfiable"
        malformed_range = client.get(
            f"/api/agent/files/{record.id}/content",
            headers={**bearer(plaintext), "Range": "items=0-1"},
        )
        assert malformed_range.status_code == 400
        assert malformed_range.headers["x-sage-error-code"] == "range_invalid"
        activity = session.scalars(
            select(Activity).where(Activity.action == "downloaded_file")
        ).one()
        assert activity.credential_name == "file-reader"
        assert activity.actor_id == user.id
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_can_recover_and_cancel_its_upload_task(tmp_path: Path, monkeypatch) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(
        session,
        user,
        ["files:upload"],
        name="upload-manager",
    )
    other_plaintext = create_token(
        session,
        user,
        ["files:upload"],
        name="other-upload-manager",
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        created = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        )
        assert created.status_code == 201
        task = created.json()
        task_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task["upload_token"],
        }

        waiting = client.get(task["status_url"], headers=task_headers)
        assert waiting.status_code == 200
        assert waiting.json()["status"] == "waiting"
        assert waiting.json()["asset_id"] == task["asset_id"]
        assert waiting.json()["archive_relative_path"] == task["archive_relative_path"]
        assert waiting.json()["files"] == []
        assert waiting.json()["result"] is None

        uploaded = client.put(
            task["file_upload_url_template"].replace("{relative_path}", "notes.txt"),
            headers=task_headers,
            content=b"temporary notes",
        )
        assert uploaded.status_code == 200
        ready = client.get(task["status_url"], headers=task_headers)
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["asset_id"] == task["asset_id"]
        assert ready.json()["archive_relative_path"] == task["archive_relative_path"]
        assert ready.json()["uploaded_file_count"] == 1
        assert ready.json()["total_size"] == len(b"temporary notes")
        assert ready.json()["files"] == [
            {
                "relative_path": "notes.txt",
                "file_size": len(b"temporary notes"),
                "checksum_sha256": None,
            }
        ]
        assert ready.json()["result"] is None

        verified = client.get(
            f"{task['status_url']}?include_checksums=true", headers=task_headers
        )
        assert verified.status_code == 200
        assert verified.json()["files"] == [
            {
                "relative_path": "notes.txt",
                "file_size": len(b"temporary notes"),
                "checksum_sha256": hashlib.sha256(b"temporary notes").hexdigest(),
            }
        ]

        wrong_pat = client.get(
            task["status_url"],
            headers={
                **bearer(other_plaintext),
                "X-Sage-Upload-Token": task["upload_token"],
            },
        )
        assert wrong_pat.status_code == 403

        synced_directories: list[Path] = []
        original_fsync_directory = transfer_service._fsync_directory

        def record_fsync_directory(directory: Path) -> None:
            synced_directories.append(directory)
            original_fsync_directory(directory)

        monkeypatch.setattr(transfer_service, "_fsync_directory", record_fsync_directory)
        cancelled = client.delete(task["cancel_url"], headers=task_headers)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert not (tmp_path / ".uploads").exists()
        assert tmp_path / ".uploads" in synced_directories
        assert tmp_path in synced_directories

        status_after_cancel = client.get(task["status_url"], headers=task_headers)
        replayed_cancel = client.delete(task["cancel_url"], headers=task_headers)
        rejected_upload = client.put(
            task["file_upload_url_template"].replace("{relative_path}", "late.txt"),
            headers=task_headers,
            content=b"late",
        )
        assert status_after_cancel.json()["status"] == "cancelled"
        assert status_after_cancel.json()["asset_id"] == task["asset_id"]
        assert (
            status_after_cancel.json()["archive_relative_path"] == (task["archive_relative_path"])
        )
        assert status_after_cancel.json()["files"] == []
        assert status_after_cancel.json()["result"] is None
        assert replayed_cancel.status_code == 200
        assert rejected_upload.status_code == 403
        upload_task = session.get(UploadTask, UUID(task["upload_id"]))
        assert upload_task is not None
        assert upload_task.status == "cancelled"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_cancel_preserves_files_when_status_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(session, user, ["files:upload"], name="commit-failure-token")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        created = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        )
        task = created.json()
        task_id = UUID(task["upload_id"])
        task_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task["upload_token"],
        }
        upload_url = task["file_upload_url_template"].replace("{relative_path}", "notes.txt")
        assert client.put(upload_url, headers=task_headers, content=b"keep me").status_code == 200
        staged_file = tmp_path / ".uploads" / str(task_id) / "notes.txt"
        original_commit = session.commit

        def fail_cancel_commit() -> None:
            upload_task = session.get(UploadTask, task_id)
            if upload_task and upload_task.status == "cancelled":
                raise RuntimeError("forced cancellation commit failure")
            original_commit()

        monkeypatch.setattr(session, "commit", fail_cancel_commit)

        with pytest.raises(RuntimeError, match="forced cancellation commit failure"):
            client.delete(task["cancel_url"], headers=task_headers)

        upload_task = session.get(UploadTask, task_id)
        assert upload_task is not None
        assert upload_task.status == "active"
        assert staged_file.read_bytes() == b"keep me"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_agent_upload_cancel_cleanup_failure_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    user, asset = create_user_and_asset(session)
    monkeypatch.setattr(settings, "auth_session_secret", "agent-test-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    plaintext = create_token(session, user, ["files:upload"], name="cleanup-retry-token")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        created = client.post(
            "/api/agent/uploads",
            headers=bearer(plaintext),
            json={"asset_id": str(asset.id), "target_subdirectory": "original"},
        )
        task = created.json()
        task_id = UUID(task["upload_id"])
        task_headers = {
            **bearer(plaintext),
            "X-Sage-Upload-Token": task["upload_token"],
        }
        upload_url = task["file_upload_url_template"].replace("{relative_path}", "notes.txt")
        assert client.put(upload_url, headers=task_headers, content=b"remove me").status_code == 200
        original_rmtree = transfer_service.shutil.rmtree

        def fail_cleanup(path: Path) -> None:
            raise OSError("forced cleanup failure")

        monkeypatch.setattr(transfer_service.shutil, "rmtree", fail_cleanup)
        first_cancel = client.delete(task["cancel_url"], headers=task_headers)

        assert first_cancel.status_code == 409
        assert "已取消" in first_cancel.json()["detail"]
        upload_task = session.get(UploadTask, task_id)
        assert upload_task is not None
        assert upload_task.status == "cancelled"
        assert (tmp_path / ".uploads" / str(task_id) / "notes.txt").is_file()

        monkeypatch.setattr(transfer_service.shutil, "rmtree", original_rmtree)
        retried = client.delete(task["cancel_url"], headers=task_headers)

        assert retried.status_code == 200
        assert retried.json()["status"] == "cancelled"
        assert not (tmp_path / ".uploads").exists()
    finally:
        app.dependency_overrides.clear()
        session.close()
