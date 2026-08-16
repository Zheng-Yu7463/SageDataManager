from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import agent, archive, assets, auth, dashboard, files, health
from app.api.routes import settings as settings_routes
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, Visibility
from app.domain.models import Asset, User
from app.main import app
from app.services.security import create_session_token
from app.services.storage import storage_index_guard

API_ROUTERS = (
    agent.router,
    archive.router,
    assets.router,
    auth.router,
    dashboard.router,
    files.router,
    health.router,
    settings_routes.router,
)
AUTH_DEPENDENCIES = {"require_admin", "require_instance_owner", "require_agent"}
PUBLIC_API_ROUTES = {
    ("GET", "/auth/invitations"),
    ("GET", "/auth/invitations/{token}"),
    ("GET", "/auth/setup-status"),
    ("GET", "/files/{file_id}/content"),
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/settings/branding"),
    ("GET", "/settings/branding/logo/{content_digest}"),
    ("HEAD", "/files/{file_id}/content"),
    ("POST", "/auth/invitations/accept"),
    ("POST", "/auth/invitations/{token}/accept"),
    ("POST", "/auth/login"),
    ("POST", "/auth/setup"),
}


def _dependency_names(dependant) -> set[str]:
    names: set[str] = set()
    for dependency in dependant.dependencies:
        name = getattr(dependency.call, "__name__", "")
        if name:
            names.add(name)
        names.update(_dependency_names(dependency))
    return names


def test_api_routes_require_authentication_unless_explicitly_public() -> None:
    actual_public_routes: set[tuple[str, str]] = set()
    for router in API_ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute)
            protected = bool(_dependency_names(route.dependant) & AUTH_DEPENDENCIES)
            if not protected:
                actual_public_routes.update((method, route.path) for method in route.methods)

    assert actual_public_routes == PUBLIC_API_ROUTES


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_invalid_session_is_rejected_when_signing_secret_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_session_secret", "")
    monkeypatch.setattr(settings, "fixed_account_password", "")

    response = TestClient(app).get(
        "/api/assets",
        headers={"X-Sage-Session": "invalid-payload.invalid-signature"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "登录已失效，请重新登录。"}


def test_management_reads_require_an_active_administrator(tmp_path, monkeypatch) -> None:
    session = make_session()
    administrator = User(
        username="zhengyu",
        name="郑宇",
        email="zhengyu@sage.lab",
        role="admin",
        is_active=True,
    )
    asset = Asset(
        type=AssetType.DATASET,
        slug="restricted-research-data",
        title="受限研究数据",
        summary="仅供管理后台使用",
        status="active",
        visibility=Visibility.RESTRICTED,
        owner=administrator,
    )
    session.add(asset)
    session.commit()

    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    app.dependency_overrides[get_session] = lambda: session
    protected_paths = [
        "/api/assets",
        "/api/assets/choices?query=data",
        f"/api/assets/{asset.id}",
        "/api/dashboard",
        "/api/archive/health",
        "/api/archive/unclaimed",
    ]
    try:
        client = TestClient(app)
        for path in protected_paths:
            assert client.get(path).status_code == 401

        headers = {"X-Sage-Session": create_session_token("zhengyu")}
        for path in protected_paths:
            assert client.get(path, headers=headers).status_code == 200

        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/setup-status").status_code == 200
        assert client.get("/api/settings/branding").status_code == 200
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_management_asset_updates_require_current_revision(tmp_path, monkeypatch) -> None:
    session = make_session()
    administrator = User(
        username="editor",
        name="资产编辑员",
        email="editor@example.org",
        role="admin",
        is_active=True,
    )
    asset = Asset(
        type=AssetType.DATASET,
        slug="concurrent-dataset",
        title="并发编辑数据集",
        summary="初始摘要",
        status="active",
        visibility=Visibility.LAB,
        owner=administrator,
        details={},
    )
    session.add(asset)
    session.commit()

    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    app.dependency_overrides[get_session] = lambda: session
    session_headers = {"X-Sage-Session": create_session_token("editor")}
    try:
        client = TestClient(app)
        detail = client.get(f"/api/assets/{asset.id}", headers=session_headers)
        original_revision = detail.json()["updated_at"]

        missing_revision = client.patch(
            f"/api/assets/{asset.id}",
            headers=session_headers,
            json={"summary": "不能无版本覆盖"},
        )
        updated = client.patch(
            f"/api/assets/{asset.id}",
            headers={
                **session_headers,
                "X-Sage-Asset-Revision": original_revision,
            },
            json={"summary": "第一个管理员的修改"},
        )
        stale = client.patch(
            f"/api/assets/{asset.id}",
            headers={
                **session_headers,
                "X-Sage-Asset-Revision": original_revision,
            },
            json={"summary": "过期页面的覆盖"},
        )

        assert missing_revision.status_code == 422
        assert updated.status_code == 200
        assert updated.json()["summary"] == "第一个管理员的修改"
        assert updated.json()["updated_at"] != original_revision
        assert stale.status_code == 409
        assert "重新读取详情" in stale.json()["detail"]
        session.expire_all()
        assert session.get(Asset, asset.id).summary == "第一个管理员的修改"
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_duplicate_archive_scan_is_rejected(tmp_path, monkeypatch) -> None:
    session = make_session()
    administrator = User(
        username="scanner",
        name="扫描管理员",
        email="scanner@example.org",
        role="admin",
        is_active=True,
    )
    session.add(administrator)
    session.commit()

    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with storage_index_guard(session, shared=True):
            response = TestClient(app).post(
                "/api/archive/scans",
                headers={"X-Sage-Session": create_session_token("scanner")},
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "已有归档扫描正在运行，请等待完成。"
    finally:
        app.dependency_overrides.clear()
        session.close()
