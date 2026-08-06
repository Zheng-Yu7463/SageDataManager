from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.domain.enums import AssetType, HealthStatus, Visibility
from app.domain.models import Asset, FileRecord, UnclaimedFile, User
from app.services.archive import scan_storage
from app.services.unclaimed import claim_unclaimed_file, list_unclaimed_files, sync_unclaimed_files


def make_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def create_asset(session: Session) -> Asset:
    owner = User(name="归档管理员", email="archive-admin@sage.lab")
    asset = Asset(
        type=AssetType.PROJECT,
        slug="field-notes",
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
