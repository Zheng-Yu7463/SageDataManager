from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, Visibility
from app.domain.models import Asset, User
from app.main import app
from app.services.security import create_session_token


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_management_reads_require_an_active_administrator(
    tmp_path, monkeypatch
) -> None:
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
        assert client.get("/api/auth/registration-status").status_code == 200
        assert client.get("/api/settings/branding").status_code == 200
        assert client.get("/api/settings/branding/logo").status_code in {200, 404}
    finally:
        app.dependency_overrides.clear()
        session.close()
