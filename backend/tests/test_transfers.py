from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.domain.enums import AssetType, HealthStatus, Visibility
from app.domain.models import Activity, Asset, FileRecord, User
from app.domain.schemas import UploadCommandRequest
from app.services.archive import scan_storage
from app.services.transfers import (
    UploadCommandError,
    UploadConflictError,
    UploadContentError,
    UploadNotReadyError,
    finalize_upload,
    generate_upload_command,
)


def make_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def create_asset(session: Session) -> Asset:
    asset = Asset(
        type=AssetType.DATASET,
        slug="soil-samples-2026",
        title="土壤样本观测数据集",
        summary="用于验证上传闭环。",
        status="draft",
        visibility=Visibility.LAB,
        owner=User(
            username="zhengyu",
            name="归档管理员",
            email="archive-admin@sage.lab",
        ),
    )
    session.add(asset)
    session.flush()
    return asset


def prepare_upload(
    session: Session,
    asset: Asset,
    *,
    source_path: str = "/mnt/research data/samples.csv",
    target_subdirectory: str = "raw/2026-08",
    recursive: bool = False,
):
    return generate_upload_command(
        session,
        UploadCommandRequest(
            asset_id=asset.id,
            source_path=source_path,
            target_subdirectory=target_subdirectory,
            recursive=recursive,
        ),
        ssh_host="192.168.1.213",
        ssh_user="zhengyu",
        ssh_port=22,
        destination_root="/srv/sage-archive",
        actor=asset.owner,
    )


def staging_directory(storage_root: Path, upload_id: UUID) -> Path:
    return storage_root / ".uploads" / str(upload_id)


@pytest.fixture(autouse=True)
def upload_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_session_secret", "upload-test-secret")


def test_generate_upload_command_targets_isolated_staging_directory() -> None:
    session = make_session()
    asset = create_asset(session)

    result = prepare_upload(session, asset)

    assert result.archive_relative_path == "dataset/soil-samples-2026/raw/2026-08"
    assert result.staging_relative_path == f".uploads/{result.upload_id}"
    assert "mkdir -p" in result.command
    assert "scp -P 22 -- '/mnt/research data/samples.csv'" in result.command
    assert f"zhengyu@192.168.1.213:/srv/sage-archive/.uploads/{result.upload_id}/" in result.command
    assert "/srv/sage-archive/dataset/soil-samples-2026" not in result.command


def test_generate_upload_command_rejects_directory_escape() -> None:
    session = make_session()
    asset = create_asset(session)

    with pytest.raises(UploadCommandError, match="相对路径"):
        prepare_upload(session, asset, target_subdirectory="../escape")


def test_generate_upload_command_requires_a_type_specific_directory() -> None:
    session = make_session()
    asset = create_asset(session)

    with pytest.raises(UploadCommandError, match="dataset 资产的一级归档目录"):
        prepare_upload(session, asset, target_subdirectory="manuscript")


def test_generate_upload_command_records_target_without_local_source() -> None:
    session = make_session()
    asset = create_asset(session)

    prepare_upload(session, asset, source_path="/private/local/path/soil-samples.csv")
    session.commit()

    activity = session.scalar(select(Activity))
    assert activity is not None
    assert activity.action == "prepared_upload"
    assert "dataset/soil-samples-2026/raw/2026-08" in activity.description
    assert "/private/local/path" not in activity.description


def test_finalize_upload_reports_empty_staging_directory(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staging_directory(tmp_path, prepared.upload_id).mkdir(parents=True)

    with pytest.raises(UploadNotReadyError, match="尚未检测到文件"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )


def test_finalize_upload_moves_and_indexes_a_file(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("sample,value\nA,1\n")

    result = finalize_upload(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )

    destination = tmp_path / "dataset/soil-samples-2026/raw/2026-08/samples.csv"
    record = session.scalar(select(FileRecord))
    assert destination.read_text() == "sample,value\nA,1\n"
    assert not staging_directory(tmp_path, prepared.upload_id).exists()
    assert result.imported_file_count == 1
    assert result.total_size == destination.stat().st_size
    assert result.relative_paths == [
        "dataset/soil-samples-2026/raw/2026-08/samples.csv"
    ]
    assert record is not None
    assert record.asset_id == asset.id
    assert record.file_kind == "data"
    assert record.health_status == HealthStatus.HEALTHY
    assert session.scalar(
        select(func.count()).select_from(Activity).where(Activity.action == "completed_upload")
    ) == 1


def test_finalize_upload_preserves_uploaded_directory_structure(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset, recursive=True)
    staged_root = staging_directory(tmp_path, prepared.upload_id)
    (staged_root / "experiment-a/results").mkdir(parents=True)
    (staged_root / "experiment-a/README.md").write_text("experiment")
    (staged_root / "experiment-a/results/metrics.json").write_text('{"score": 1}')

    result = finalize_upload(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )

    assert result.relative_paths == [
        "dataset/soil-samples-2026/raw/2026-08/experiment-a/README.md",
        "dataset/soil-samples-2026/raw/2026-08/experiment-a/results/metrics.json",
    ]
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 2


def test_finalize_upload_rejects_symlink_without_moving_files(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged_root = staging_directory(tmp_path, prepared.upload_id)
    staged_root.mkdir(parents=True)
    (staged_root / "valid.csv").write_text("value\n1\n")
    (staged_root / "linked.csv").symlink_to(staged_root / "valid.csv")

    with pytest.raises(UploadContentError, match="符号链接"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )

    assert (staged_root / "valid.csv").exists()
    assert not (tmp_path / "dataset").exists()
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 0


def test_finalize_upload_blocks_all_filesystem_conflicts_before_move(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged_root = staging_directory(tmp_path, prepared.upload_id)
    staged_root.mkdir(parents=True)
    (staged_root / "existing.csv").write_text("new")
    (staged_root / "new.csv").write_text("new")
    destination = tmp_path / "dataset/soil-samples-2026/raw/2026-08/existing.csv"
    destination.parent.mkdir(parents=True)
    destination.write_text("original")

    with pytest.raises(UploadConflictError, match="existing.csv"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )

    assert destination.read_text() == "original"
    assert (staged_root / "existing.csv").exists()
    assert (staged_root / "new.csv").exists()
    assert not destination.with_name("new.csv").exists()


def test_finalize_upload_blocks_database_path_conflict(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("new")
    session.add(
        FileRecord(
            asset=asset,
            relative_path="dataset/soil-samples-2026/raw/2026-08/samples.csv",
            file_name="samples.csv",
            file_kind="data",
            file_size=3,
        )
    )
    session.commit()

    with pytest.raises(UploadConflictError, match="samples.csv"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )

    assert staged.exists()
    assert not (tmp_path / "dataset").exists()


def test_finalize_upload_rejects_destination_parent_symlink(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("new")
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "dataset").symlink_to(external, target_is_directory=True)

    with pytest.raises(UploadConflictError, match="samples.csv"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )

    assert staged.exists()
    assert list(external.iterdir()) == []


def test_finalize_upload_rolls_back_moved_files_when_indexing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("new")

    def fail_flush(*args, **kwargs):
        raise RuntimeError("database unavailable")

    original_flush = session.flush
    monkeypatch.setattr(session, "flush", fail_flush)

    with pytest.raises(RuntimeError, match="database unavailable"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )

    assert staged.exists()
    assert not (tmp_path / "dataset").exists()
    monkeypatch.setattr(session, "flush", original_flush)
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 0


def test_archive_scan_excludes_upload_staging_files(tmp_path: Path) -> None:
    session = make_session()
    create_asset(session)
    staged = staging_directory(tmp_path, uuid4()) / "partial.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("partial")

    result = scan_storage(session, tmp_path)
    session.commit()

    assert result.files_discovered == 0
    assert result.files_unclaimed == 0
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 0
