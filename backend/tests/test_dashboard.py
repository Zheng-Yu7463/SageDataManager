from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, Visibility
from app.domain.models import Asset, Tag, User
from app.main import app
from app.services.security import create_session_token


def make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_dashboard_popular_tags_exclude_archived_assets(monkeypatch) -> None:
    session = make_session()
    user = User(
        username="zhengyu",
        name="郑宇",
        email="zhengyu@sage.lab",
        role="admin",
        is_active=True,
    )
    active = Asset(
        type=AssetType.LITERATURE,
        slug="active-literature",
        title="Active Literature",
        status="published",
        visibility=Visibility.LAB,
        owner=user,
        tags=[Tag(name="active-tag")],
    )
    archived = Asset(
        type=AssetType.LITERATURE,
        slug="archived-literature",
        title="Archived Literature",
        status="published",
        visibility=Visibility.LAB,
        owner=user,
        tags=[Tag(name="archived-tag")],
        archived_at=datetime.now(UTC),
    )
    session.add_all([active, archived])
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "dashboard-test-secret")
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).get(
            "/api/dashboard",
            headers={"X-Sage-Session": create_session_token("zhengyu")},
        )

        assert response.status_code == 200
        assert response.json()["popular_tags"] == [["active-tag", 1]]
    finally:
        app.dependency_overrides.clear()
        session.close()
