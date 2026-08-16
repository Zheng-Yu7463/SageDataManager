from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes.dashboard import dashboard
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.domain.enums import AssetType, Visibility
from app.domain.models import Asset, FileRecord, Tag, User
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


def test_dashboard_aggregates_recent_file_stats_without_loading_files() -> None:
    session = make_session()
    user = User(
        username="dashboard-admin",
        name="Dashboard Admin",
        email="dashboard-admin@sage.lab",
        role="admin",
        is_active=True,
    )
    asset = Asset(
        type=AssetType.DATASET,
        slug="large-dataset",
        title="Large Dataset",
        status="active",
        visibility=Visibility.LAB,
        owner=user,
    )
    session.add(asset)
    session.flush()
    session.add_all(
        [
            FileRecord(
                asset_id=asset.id,
                relative_path="dataset/large-dataset/raw/first.csv",
                file_name="first.csv",
                file_kind="data",
                file_size=128,
            ),
            FileRecord(
                asset_id=asset.id,
                relative_path="dataset/large-dataset/raw/second.csv",
                file_name="second.csv",
                file_kind="data",
                file_size=256,
            ),
        ]
    )
    session.commit()
    asset_id = asset.id
    session.expunge_all()
    tracked_asset = session.get(Asset, asset_id)
    assert tracked_asset is not None
    assert "files" in inspect(tracked_asset).unloaded

    result = dashboard(session)

    assert result.total_storage_bytes == 384
    assert len(result.recent_assets) == 1
    assert result.recent_assets[0].file_count == 2
    assert result.recent_assets[0].total_size == 384
    assert "files" in inspect(tracked_asset).unloaded


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
