from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
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
from app.services import archive as archive_service
from app.services import storage as storage_service
from app.services.archive import ScanAlreadyRunningError, scan_storage
from app.services.security import create_session_token
from app.services.storage import (
    StorageFileEntry,
    StorageIndexBusyError,
    storage_index_guard,
    storage_index_lock_statement,
)
from app.services.unclaimed import (
    ClaimSourceFileError,
    FilePathConflictError,
    claim_unclaimed_file,
    list_unclaimed_files,
    locked_claim_asset_statement,
    locked_unclaimed_file_statement,
    locked_unclaimed_files_statement,
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


def unclaimed_items(session: Session):
    return list_unclaimed_files(session, page=1, page_size=100)[0]


def test_unclaimed_file_listing_is_bounded_and_reports_total() -> None:
    session = make_session()
    now = datetime.now(UTC)
    for index in range(3):
        session.add(
            UnclaimedFile(
                relative_path=f"incoming/file-{index}.csv",
                file_name=f"file-{index}.csv",
                file_kind="data",
                file_size=index,
                last_seen_at=now - timedelta(minutes=index),
            )
        )
    session.commit()

    first_page, total = list_unclaimed_files(session, page=1, page_size=2)
    second_page, second_total = list_unclaimed_files(session, page=2, page_size=2)

    assert total == second_total == 3
    assert [item.file_name for item in first_page] == ["file-0.csv", "file-1.csv"]
    assert [item.file_name for item in second_page] == ["file-2.csv"]


def test_storage_index_lock_allows_shared_finalizers_and_excludes_scans(
    tmp_path: Path,
) -> None:
    session = make_session()
    shared_sql = str(
        storage_index_lock_statement(shared=True).compile(dialect=postgresql.dialect())
    )
    exclusive_sql = str(
        storage_index_lock_statement(shared=False).compile(dialect=postgresql.dialect())
    )

    assert "pg_try_advisory_xact_lock_shared" in shared_sql
    assert "pg_try_advisory_xact_lock" in exclusive_sql
    assert "pg_try_advisory_xact_lock_shared" not in exclusive_sql

    with storage_index_guard(session, shared=True):
        with storage_index_guard(session, shared=True):
            pass
        with (
            pytest.raises(StorageIndexBusyError),
            storage_index_guard(session, shared=False),
        ):
            pass
        with pytest.raises(ScanAlreadyRunningError):
            scan_storage(session, tmp_path)

    with (
        storage_index_guard(session, shared=False),
        pytest.raises(StorageIndexBusyError),
        storage_index_guard(session, shared=True),
    ):
        pass


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


def test_archive_scan_uses_one_descriptor_anchored_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "archive"
    indexed = storage_root / "project" / "field-notes" / "documents" / "notes.txt"
    unclaimed = storage_root / "incoming" / "orphan.csv"
    indexed.parent.mkdir(parents=True)
    unclaimed.parent.mkdir(parents=True)
    indexed.write_text("indexed")
    unclaimed.write_text("unclaimed")
    session = make_session()
    create_asset(session)
    original_fwalk = storage_service.os.fwalk
    traversal_count = 0

    def tracked_fwalk(*args, **kwargs):
        nonlocal traversal_count
        traversal_count += 1
        assert args == (".",)
        assert kwargs["follow_symlinks"] is False
        assert isinstance(kwargs["dir_fd"], int)
        return original_fwalk(*args, **kwargs)

    monkeypatch.setattr(storage_service.os, "fwalk", tracked_fwalk)

    result = scan_storage(session, storage_root)

    assert traversal_count == 1
    assert result.files_discovered == 2
    assert result.files_indexed == 1
    assert result.files_unclaimed == 1
    assert [item.relative_path for item in unclaimed_items(session)] == ["incoming/orphan.csv"]


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
    assert [item.relative_path for item in unclaimed_items(session)] == [
        "incoming/field-notes.txt"
    ]

    result = claim_unclaimed_file(session, storage_root, unclaimed.id, asset.id)
    session.commit()

    assert result.asset_id == asset.id
    assert result.file.file_name == "field-notes.txt"
    assert unclaimed_items(session) == []

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


def test_scan_skips_file_that_disappears_during_metadata_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "archive"
    source = storage_root / "project" / "field-notes" / "documents" / "notes.txt"
    source.parent.mkdir(parents=True)
    source.write_text("temporary")
    session = make_session()
    create_asset(session)
    original_stat = storage_service._stat_storage_entry

    def disappear_on_metadata_read(name: str, directory_descriptor: int):
        if name == source.name:
            raise FileNotFoundError
        return original_stat(name, directory_descriptor)

    monkeypatch.setattr(
        storage_service,
        "_stat_storage_entry",
        disappear_on_metadata_read,
    )

    result = scan_storage(session, storage_root)

    assert result.status == "completed"
    assert result.files_skipped == 1
    assert result.files_indexed == 0
    assert session.scalars(select(FileRecord)).all() == []


def test_scan_does_not_follow_file_replaced_with_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "archive"
    source = storage_root / "project" / "field-notes" / "documents" / "notes.txt"
    source.parent.mkdir(parents=True)
    source.write_text("inside")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside metadata must not be indexed")
    session = make_session()
    create_asset(session)
    original_stat = storage_service._stat_storage_entry
    replaced = False

    def replace_before_metadata(name: str, directory_descriptor: int):
        nonlocal replaced
        if name == source.name and not replaced:
            replaced = True
            source.unlink()
            source.symlink_to(outside)
        return original_stat(name, directory_descriptor)

    monkeypatch.setattr(storage_service, "_stat_storage_entry", replace_before_metadata)

    result = scan_storage(session, storage_root)

    assert replaced is True
    assert result.files_skipped == 1
    assert result.files_discovered == 0
    assert result.files_indexed == 0
    assert session.scalars(select(FileRecord)).all() == []


def test_scan_skips_relative_paths_longer_than_the_database_column(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "archive"
    storage_root.mkdir()
    deep_parts = [character * 200 for character in "abcde"]
    relative_path = PurePosixPath(
        "project",
        "field-notes",
        "documents",
        *deep_parts,
        "notes.txt",
    )
    source = storage_root / "notes.txt"
    source.write_text("too deep")
    entry = StorageFileEntry(relative_path=relative_path, metadata=source.stat())
    monkeypatch.setattr(
        archive_service,
        "iter_storage_file_entries",
        lambda _root, on_skip: iter([entry]),
    )
    session = make_session()
    create_asset(session)

    result = scan_storage(session, storage_root)

    assert len(relative_path.as_posix()) > 1000
    assert result.status == "completed"
    assert result.files_discovered == 1
    assert result.files_skipped == 1
    assert result.files_indexed == 0
    assert session.scalars(select(FileRecord)).all() == []
    assert session.scalars(select(UnclaimedFile)).all() == []


def test_claim_rejects_source_changed_since_scan(tmp_path: Path) -> None:
    storage_root = tmp_path / "archive"
    source = storage_root / "incoming" / "temporary.csv"
    source.parent.mkdir(parents=True)
    source.write_text("value\n1\n")
    session = make_session()
    asset = create_asset(session)
    sync_unclaimed_files(session, storage_root)
    session.flush()
    unclaimed = session.scalar(select(UnclaimedFile))
    assert unclaimed is not None

    source.write_text("different content after scan\n")

    with pytest.raises(ClaimSourceFileError):
        claim_unclaimed_file(session, storage_root, unclaimed.id, asset.id)

    assert session.scalars(select(FileRecord)).all() == []
    assert unclaimed.claimed_asset_id is None


def test_claim_rejects_source_replaced_with_symlink_since_scan(tmp_path: Path) -> None:
    storage_root = tmp_path / "archive"
    source = storage_root / "incoming" / "temporary.csv"
    replacement = storage_root / "incoming" / "replacement.csv"
    source.parent.mkdir(parents=True)
    source.write_text("value\n1\n")
    session = make_session()
    asset = create_asset(session)
    sync_unclaimed_files(session, storage_root)
    session.flush()
    unclaimed = session.scalar(select(UnclaimedFile))
    assert unclaimed is not None

    replacement.write_text("replacement\n")
    source.unlink()
    source.symlink_to(replacement.name)

    with pytest.raises(ClaimSourceFileError):
        claim_unclaimed_file(session, storage_root, unclaimed.id, asset.id)

    assert session.scalars(select(FileRecord)).all() == []
    assert unclaimed.claimed_asset_id is None


def test_file_paths_are_unique_and_claims_lock_source_and_target_rows() -> None:
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

    claim_statement = locked_unclaimed_file_statement(uuid4())
    asset_statement = locked_claim_asset_statement(uuid4())
    sync_statement = locked_unclaimed_files_statement()
    claim_compiled = str(claim_statement.compile(dialect=postgresql.dialect()))
    asset_compiled = str(asset_statement.compile(dialect=postgresql.dialect()))
    sync_compiled = str(sync_statement.compile(dialect=postgresql.dialect()))
    assert claim_compiled.endswith("FOR UPDATE")
    assert "assets.archived_at IS NULL" in asset_compiled
    assert asset_compiled.endswith("FOR UPDATE")
    assert sync_compiled.endswith("FOR UPDATE")


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


def test_scan_keeps_archived_asset_files_indexed_and_claimed(tmp_path: Path) -> None:
    storage_root = tmp_path / "archive"
    source = storage_root / "project" / "field-notes" / "documents" / "notes.txt"
    source.parent.mkdir(parents=True)
    source.write_text("archived research notes")
    session = make_session()
    asset = create_asset(session)

    initial_scan = scan_storage(session, storage_root)
    session.commit()
    asset.archived_at = datetime.now(UTC)
    session.commit()
    archived_scan = scan_storage(session, storage_root)
    session.commit()

    record = session.scalar(select(FileRecord))
    assert initial_scan.files_indexed == 1
    assert archived_scan.files_indexed == 1
    assert archived_scan.files_missing == 0
    assert archived_scan.files_unclaimed == 0
    assert record is not None
    assert record.asset_id == asset.id
    assert record.health_status == HealthStatus.HEALTHY
    assert unclaimed_items(session) == []


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
