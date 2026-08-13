from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
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


def image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (24, 24),
    color: tuple[int, int, int] = (46, 115, 81),
) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format=image_format)
    return output.getvalue()


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
            "slogan": "科学 · 数据 · 成长 · 卓越",
            "slogan_secondary": "Science · Archive · Growth · Excellence",
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
        record = session.get(InstanceBranding, 1)
        assert record.product_name == "Atlas"
        original_updated_at = record.updated_at
        replayed = client.patch(
            "/api/settings/branding",
            json={**payload, "product_name": "  Atlas  ", "primary_color": "#245b78"},
            headers={"X-Sage-Session": create_session_token("zhengyu")},
        )
        assert replayed.status_code == 200
        assert session.get(InstanceBranding, 1).updated_at == original_updated_at
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
        for image_format, mime_type in (
            ("PNG", "image/png"),
            ("JPEG", "image/jpeg"),
            ("WEBP", "image/webp"),
        ):
            content = image_bytes(image_format)
            uploaded = client.put(
                "/api/settings/branding/logo",
                content=content,
                headers={**headers, "Content-Type": mime_type},
            )
            logo_url = uploaded.json()["logo_url"]

            assert uploaded.status_code == 200
            assert logo_url.startswith("/api/settings/branding/logo/")
            assert len(logo_url.rsplit("/", 1)[1]) == 64

            logo = client.get(logo_url)
            assert logo.status_code == 200
            assert logo.headers["content-type"] == mime_type
            assert logo.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert logo.headers["etag"] == f'"{logo_url.rsplit("/", 1)[1]}"'
            assert logo.content == content

        removed = client.delete("/api/settings/branding/logo", headers=headers)
        assert removed.status_code == 200
        assert removed.json()["logo_url"] is None
        assert client.get(logo_url).status_code == 404
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_branding_logo_rejects_invalid_format_size_and_animation(monkeypatch) -> None:
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
        invalid_contents = [
            (b"\x89PNG\r\n\x1a\nnot-an-image", "image/png"),
            (image_bytes("PNG")[:-8], "image/png"),
            (image_bytes("PNG"), "image/jpeg"),
            (image_bytes("PNG", size=(4097, 1)), "image/png"),
            (image_bytes("PNG", size=(2049, 2049)), "image/png"),
        ]
        animation = BytesIO()
        frames = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]
        frames[0].save(
            animation,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
        invalid_contents.append((animation.getvalue(), "image/webp"))

        for content, mime_type in invalid_contents:
            response = client.put(
                "/api/settings/branding/logo",
                content=content,
                headers={**headers, "Content-Type": mime_type},
            )
            assert response.status_code == 422

        oversized = client.put(
            "/api/settings/branding/logo",
            content=b"x" * 1_000_001,
            headers={**headers, "Content-Type": "image/png"},
        )
        assert oversized.status_code == 413
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_branding_logo_url_is_content_addressed(monkeypatch) -> None:
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
    headers = {"X-Sage-Session": create_session_token("zhengyu"), "Content-Type": "image/png"}
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        first_content = image_bytes("PNG", color=(46, 115, 81))
        second_content = image_bytes("PNG", color=(36, 91, 120))
        first_url = client.put(
            "/api/settings/branding/logo", content=first_content, headers=headers
        ).json()["logo_url"]
        first_updated_at = session.get(InstanceBranding, 1).updated_at
        repeated_url = client.put(
            "/api/settings/branding/logo", content=first_content, headers=headers
        ).json()["logo_url"]
        repeated_updated_at = session.get(InstanceBranding, 1).updated_at
        second_url = client.put(
            "/api/settings/branding/logo", content=second_content, headers=headers
        ).json()["logo_url"]
        second_updated_at = session.get(InstanceBranding, 1).updated_at

        assert repeated_url == first_url
        assert repeated_updated_at == first_updated_at
        assert second_url != first_url
        assert second_updated_at > first_updated_at
        assert session.query(Activity).filter_by(action="updated_branding").count() == 2
        assert client.get(first_url).status_code == 404
        assert client.get(second_url).content == second_content
        assert client.get("/api/settings/branding/logo/not-a-digest").status_code == 422
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_removing_absent_branding_logo_is_idempotent(monkeypatch) -> None:
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
        response = TestClient(app).delete("/api/settings/branding/logo", headers=headers)

        assert response.status_code == 200
        assert response.json()["logo_url"] is None
        assert session.get(InstanceBranding, 1) is None
        assert session.query(Activity).filter_by(action="updated_branding").count() == 0
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
