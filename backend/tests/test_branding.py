from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.models import Activity, InstanceBranding, User
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


def test_branding_is_public_and_updates_require_admin(monkeypatch) -> None:
    session = make_session()
    session.add(
        User(
            username="zhengyu",
            name="郑宇",
            email="zhengyu@sage.lab",
            role="admin",
            is_active=True,
        )
    )
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        initial = client.get("/api/settings/branding")
        assert initial.status_code == 200
        assert initial.json() == {
            "product_name": "SAGE",
            "product_subtitle": "RESEARCH ARCHIVE",
            "organization_name": "SAGE Lab",
            "slogan": "数据 · 知识 · 传承",
            "slogan_secondary": "Science · Archive · Growth",
            "primary_color": "#2E7351",
            "logo_url": None,
        }

        payload = {
            "product_name": "Atlas",
            "product_subtitle": "DATA MANAGER",
            "organization_name": "Atlas Institute",
            "slogan": "研究 · 连接 · 积累",
            "slogan_secondary": "Research · Connect · Preserve",
            "primary_color": "#245B78",
        }
        assert client.patch("/api/settings/branding", json=payload).status_code == 401
        updated = client.patch(
            "/api/settings/branding",
            json=payload,
            headers={"X-Sage-Session": create_session_token("zhengyu")},
        )

        assert updated.status_code == 200
        assert updated.json() == {**payload, "logo_url": None}
        assert session.get(InstanceBranding, 1).product_name == "Atlas"
        assert session.query(Activity).filter_by(action="updated_branding").count() == 1
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_branding_logo_validates_content_and_can_be_removed(monkeypatch) -> None:
    session = make_session()
    session.add(
        User(
            username="zhengyu",
            name="郑宇",
            email="zhengyu@sage.lab",
            role="admin",
            is_active=True,
        )
    )
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    headers = {"X-Sage-Session": create_session_token("zhengyu")}
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        invalid = client.put(
            "/api/settings/branding/logo",
            content=b"not-an-image",
            headers={**headers, "Content-Type": "image/png"},
        )
        assert invalid.status_code == 422

        png = b"\x89PNG\r\n\x1a\n" + b"test-image-data"
        uploaded = client.put(
            "/api/settings/branding/logo",
            content=png,
            headers={**headers, "Content-Type": "image/png"},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["logo_url"].startswith("/api/settings/branding/logo?v=")

        logo = client.get("/api/settings/branding/logo")
        assert logo.status_code == 200
        assert logo.headers["content-type"] == "image/png"
        assert logo.content == png

        removed = client.delete("/api/settings/branding/logo", headers=headers)
        assert removed.status_code == 200
        assert removed.json()["logo_url"] is None
        assert client.get("/api/settings/branding/logo").status_code == 404
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_branding_rejects_low_contrast_color(monkeypatch) -> None:
    session = make_session()
    session.add(
        User(
            username="zhengyu",
            name="郑宇",
            email="zhengyu@sage.lab",
            role="admin",
            is_active=True,
        )
    )
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).patch(
            "/api/settings/branding",
            json={
                "product_name": "Atlas",
                "product_subtitle": "DATA MANAGER",
                "organization_name": "Atlas Institute",
                "slogan": "研究 · 连接 · 积累",
                "slogan_secondary": "Research · Connect · Preserve",
                "primary_color": "#FFFF00",
            },
            headers={"X-Sage-Session": create_session_token("zhengyu")},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        session.close()
