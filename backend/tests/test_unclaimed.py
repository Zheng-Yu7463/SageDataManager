from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import dependencies
from app.api.routes import archive as archive_routes
from app.core.config import settings
from app.db.base import Base
from app.db.constraints import violates_constraint
from app.db.session import get_session
from app.domain.enums import AssetType, HealthStatus, Visibility
from app.domain.models import Activity, Asset, FileRecord, UnclaimedFile, User
from app.main import app
from app.services.archive import scan_storage
from app.services.security import create_session_token
from app.services.unclaimed import (
    FilePathConflictError,
    claim_unclaimed_file,
    list_unclaimed_files,
    locked_unclaimed_file_statement,
    sync_unclaimed_files,
)


class DatabaseDiagnostic:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class DatabaseError(Exception):
    def __init__(self, constraint_name: str) -> None:
        self.diag = DatabaseDiagnostic(constraint_name)


def make_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def create_asset(
    session: Session,
    *,
    slug: str = "field-notes",
    owner: User | None = None,
) -> Asset:
    owner = owner or User(name="归档管理员", email=f"{slug}@sage.lab")
    asset = Asset(
        type=AssetType.PROJECT,
        slug=slug,
        title="田野笔记项目",
        summary="用于验证待认领文件的归档关联。",
        status="active",
        visibility=Visibility.LAB,
        owner=owner,
    )
    session.add(asset)
    session.flush()
    return asset


def test_claimed_file_is_indexed_on_future_scans(tmp_path: Path) -> None:
    storage_root = tmp_path / "archive"
    incoming = storage_root / "incoming"
    incoming.mkdir(parents=True)
    (incoming / "field-notes.txt").write_text("mock field notes\n")

    session = make_session()
    asset = create_asset(session)
    sync_unclaimed_files(session, storage_root)
    session.flush()

    unclaimed = session.scalar(select(UnclaimedFile))
    assert unclaimed is not None
    assert [item.relative_path for item in list_unclaimed_files(session)] == [
        "incoming/field-notes.txt"
    ]

    result = claim_unclaimed_file(session, storage_root, unclaimed.id, asset.id)
    session.commit()

    assert result.asset_id == asset.id
    assert result.file.file_name == "field-notes.txt"
    assert list_unclaimed_files(session) == []

    scan = scan_storage(session, storage_root)
    session.commit()

    file_record = session.scalar(select(FileRecord))
    assert file_record is not None
    assert file_record.asset_id == asset.id
    assert file_record.relative_path == "incoming/field-notes.txt"
    assert file_record.health_status == HealthStatus.HEALTHY
    assert scan.files_indexed == 1
    assert scan.files_unclaimed == 0


def test_sync_removes_unclaimed_records_when_source_file_disappears(tmp_path: Path) -> None:
    storage_root = tmp_path / "archive"
    incoming = storage_root / "incoming"
    incoming.mkdir(parents=True)
    source = incoming / "temporary.csv"
    source.write_text("value\n1\n")

    session = make_session()
    sync_unclaimed_files(session, storage_root)
    assert session.scalar(select(UnclaimedFile)) is not None

    source.unlink()
    sync_unclaimed_files(session, storage_root)

    assert session.scalars(select(UnclaimedFile)).all() == []


def test_file_paths_are_database_unique_and_claims_lock_the_source_row() -> None:
    session = make_session()
    first = create_asset(session, slug="first")
    second = create_asset(session, slug="second")
    session.add_all(
        [
            FileRecord(
                asset=first,
                relative_path="incoming/shared.csv",
                file_name="shared.csv",
                file_kind="data",
                file_size=1,
            ),
            FileRecord(
                asset=second,
                relative_path="incoming/shared.csv",
                file_name="shared.csv",
                file_kind="data",
                file_size=1,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        session.flush()

    statement = locked_unclaimed_file_statement(uuid4())
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert compiled.endswith("FOR UPDATE")


def test_only_the_archive_path_constraint_is_mapped_to_a_domain_conflict() -> None:
    path_conflict = IntegrityError(
        "insert", {}, DatabaseError("uq_asset_files_relative_path")
    )
    unrelated_conflict = IntegrityError("insert", {}, DatabaseError("other_constraint"))

    assert violates_constraint(path_conflict, "uq_asset_files_relative_path")
    assert not violates_constraint(unrelated_conflict, "uq_asset_files_relative_path")


def test_claim_rejects_a_path_owned_by_another_asset_without_activity(tmp_path: Path) -> None:
    storage_root = tmp_path / "archive"
    source = storage_root / "incoming" / "shared.csv"
    source.parent.mkdir(parents=True)
    source.write_text("value\n1\n")
    session = make_session()
    first = create_asset(session, slug="first")
    second = create_asset(session, slug="second", owner=first.owner)
    session.add(
        FileRecord(
            asset=first,
            relative_path="incoming/shared.csv",
            file_name="shared.csv",
            file_kind="data",
            file_size=8,
        )
    )
    sync_unclaimed_files(session, storage_root)
    session.flush()
    unclaimed = session.scalar(select(UnclaimedFile))
    assert unclaimed is not None

    with pytest.raises(FilePathConflictError):
        claim_unclaimed_file(session, storage_root, unclaimed.id, second.id, actor=first.owner)

    assert session.scalar(select(func.count()).select_from(FileRecord)) == 1
    assert session.scalar(select(func.count()).select_from(Activity)) == 0


def test_scan_keeps_one_healthy_record_for_a_claimed_path(tmp_path: Path) -> None:
    storage_root = tmp_path / "archive"
    source = storage_root / "incoming" / "claimed.txt"
    source.parent.mkdir(parents=True)
    source.write_text("claimed")
    session = make_session()
    asset = create_asset(session)
    sync_unclaimed_files(session, storage_root)
    session.flush()
    unclaimed = session.scalar(select(UnclaimedFile))
    assert unclaimed is not None
    claim_unclaimed_file(session, storage_root, unclaimed.id, asset.id)
    session.commit()

    result = scan_storage(session, storage_root)
    session.commit()

    records = session.scalars(select(FileRecord)).all()
    assert len(records) == 1
    assert records[0].health_status == HealthStatus.HEALTHY
    assert result.files_indexed == 1
    assert result.files_missing == 0


def test_claim_route_maps_path_ownership_conflict_to_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    admin = User(
        username="zhengyu",
        name="郑宇",
        email="zhengyu@sage.lab",
        role="admin",
        is_active=True,
    )
    session.add(admin)
    session.commit()
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(
        archive_routes,
        "claim_unclaimed_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(FilePathConflictError()),
    )
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[dependencies.require_admin] = lambda: admin
    try:
        response = TestClient(app).post(
            "/api/archive/unclaimed/00000000-0000-0000-0000-000000000001/claim",
            json={"asset_id": "00000000-0000-0000-0000-000000000002"},
            headers={"X-Sage-Session": create_session_token("zhengyu")},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "该归档路径已归属于其他资产。"
    finally:
        app.dependency_overrides.clear()
        session.close()
