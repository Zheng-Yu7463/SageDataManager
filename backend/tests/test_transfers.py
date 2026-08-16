import hashlib
import os
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.domain.enums import AssetType, HealthStatus, Visibility
from app.domain.models import Activity, Asset, FileRecord, UploadTask, User
from app.domain.schemas import UploadCommandRequest
from app.services.archive import scan_storage
from app.services.storage import UPLOAD_LOCKS_DIRECTORY, storage_index_guard
from app.services.transfers import (
    UPLOAD_COMPLETION_MARKER,
    UploadBusyError,
    UploadCommandError,
    UploadConflictError,
    UploadContentError,
    UploadNotReadyError,
    cleanup_expired_upload_tasks,
    complete_agent_file_upload,
    finalize_upload,
    generate_upload_command,
    upload_status,
)


def test_agent_file_publish_fsyncs_created_directories_and_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"durable content"
    temporary_file = tmp_path / "temporary-upload"
    temporary_file.write_bytes(content)
    upload_id = uuid4()
    destination = tmp_path / ".uploads" / str(upload_id) / "nested" / "file.bin"
    directory_syncs = 0
    original_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr("app.services.transfers.os.fsync", record_fsync)

    result = complete_agent_file_upload(
        upload_id,
        "nested/file.bin",
        temporary_file,
        destination,
        hashlib.sha256(content).hexdigest(),
    )

    assert result.file_size == len(content)
    assert destination.read_bytes() == content
    assert not temporary_file.exists()
    assert directory_syncs >= 4


def test_agent_file_publish_rolls_back_link_when_directory_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary_file = tmp_path / "temporary-upload"
    temporary_file.write_bytes(b"content")
    upload_id = uuid4()
    destination = tmp_path / ".uploads" / str(upload_id) / "file.bin"
    destination.parent.mkdir(parents=True)

    def fail_fsync(descriptor: int) -> None:
        raise OSError("simulated directory sync failure")

    monkeypatch.setattr("app.services.transfers.os.fsync", fail_fsync)

    with pytest.raises(OSError, match="directory sync failure"):
        complete_agent_file_upload(
            upload_id,
            "file.bin",
            temporary_file,
            destination,
            hashlib.sha256(b"content").hexdigest(),
        )

    assert temporary_file.exists()
    assert not destination.exists()


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


def mark_upload_complete(storage_root: Path, upload_id: UUID) -> None:
    (staging_directory(storage_root, upload_id) / UPLOAD_COMPLETION_MARKER).touch()


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


def test_finalize_upload_rejects_an_active_archive_scan(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)

    with (
        storage_index_guard(session, shared=False),
        pytest.raises(UploadBusyError, match="归档扫描正在运行"),
    ):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )


def test_finalize_upload_reports_empty_staging_directory(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staging_directory(tmp_path, prepared.upload_id).mkdir(parents=True)
    mark_upload_complete(tmp_path, prepared.upload_id)

    with pytest.raises(UploadNotReadyError, match="尚未检测到文件"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )


def test_finalize_upload_requires_scp_completion_marker(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "partial.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("still transferring")

    with pytest.raises(UploadNotReadyError, match="传输尚未完成"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )

    assert staged.exists()
    assert not (tmp_path / "dataset").exists()
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 0


def test_finalize_upload_moves_and_indexes_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("sample,value\nA,1\n")
    mark_upload_complete(tmp_path, prepared.upload_id)
    directory_syncs = 0
    original_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr("app.services.transfers.os.fsync", record_fsync)

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
    assert result.relative_paths == ["dataset/soil-samples-2026/raw/2026-08/samples.csv"]
    expected_checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    assert result.checksums[result.relative_paths[0]] == expected_checksum
    assert record is not None
    assert record.checksum == expected_checksum
    assert record.asset_id == asset.id
    assert record.file_kind == "data"
    assert record.health_status == HealthStatus.HEALTHY
    assert directory_syncs >= 5
    assert (
        session.scalar(
            select(func.count()).select_from(Activity).where(Activity.action == "completed_upload")
        )
        == 1
    )


def test_archive_scan_clears_checksum_only_when_file_snapshot_changes(
    tmp_path: Path,
) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("sample,value\nA,1\n")
    mark_upload_complete(tmp_path, prepared.upload_id)
    finalize_upload(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )
    destination = tmp_path / "dataset/soil-samples-2026/raw/2026-08/samples.csv"
    record = session.scalar(select(FileRecord))
    assert record is not None
    original_checksum = record.checksum
    assert original_checksum is not None

    scan_storage(session, tmp_path)
    assert record.checksum == original_checksum

    destination.write_text("externally modified archive content")
    scan_storage(session, tmp_path)

    assert record.checksum is None
    assert record.file_size == destination.stat().st_size
    assert record.health_status == HealthStatus.HEALTHY


def test_finalize_upload_replay_retries_staging_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staging = staging_directory(tmp_path, prepared.upload_id)
    staged = staging / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("sample,value\nA,1\n")
    mark_upload_complete(tmp_path, prepared.upload_id)
    original_rmtree = shutil.rmtree
    cleanup_attempts = 0

    def fail_first_cleanup(path: Path) -> None:
        nonlocal cleanup_attempts
        cleanup_attempts += 1
        if cleanup_attempts == 1:
            raise OSError("simulated cleanup failure")
        original_rmtree(path)

    monkeypatch.setattr("app.services.transfers.shutil.rmtree", fail_first_cleanup)

    with pytest.raises(UploadContentError, match="已完成入库"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )
    assert staging.is_dir()

    replayed = finalize_upload(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )

    assert replayed.imported_file_count == 1
    assert cleanup_attempts == 2
    assert not staging.exists()


def test_cleanup_expired_upload_tasks_preserves_active_tasks(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    expired = prepare_upload(session, asset)
    active = prepare_upload(session, asset)
    expired_task = session.get(UploadTask, expired.upload_id)
    active_task = session.get(UploadTask, active.upload_id)
    assert expired_task is not None
    assert active_task is not None
    expired_task.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()

    expired_file = staging_directory(tmp_path, expired.upload_id) / "expired.csv"
    active_file = staging_directory(tmp_path, active.upload_id) / "active.csv"
    expired_file.parent.mkdir(parents=True)
    active_file.parent.mkdir(parents=True)
    expired_file.write_text("expired")
    active_file.write_text("active")

    cleaned = cleanup_expired_upload_tasks(session, tmp_path)

    assert cleaned == 1
    assert session.get(UploadTask, expired.upload_id) is None
    assert session.get(UploadTask, active.upload_id) is not None
    assert not expired_file.exists()
    assert active_file.read_text() == "active"


def test_upload_status_reports_waiting_ready_and_completed(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)

    waiting = upload_status(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )
    assert waiting.status == "waiting"
    assert waiting.uploaded_file_count == 0

    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("sample,value\nA,1\n")
    transferring = upload_status(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )
    assert transferring.status == "waiting"
    assert transferring.uploaded_file_count == 1

    mark_upload_complete(tmp_path, prepared.upload_id)
    ready = upload_status(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )
    assert ready.status == "ready"
    assert ready.total_size == staged.stat().st_size

    finalize_upload(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )
    completed = upload_status(
        session,
        tmp_path,
        prepared.upload_id,
        prepared.upload_token,
        actor=asset.owner,
    )
    assert completed.status == "completed"
    assert completed.uploaded_file_count == 1


def test_finalize_upload_rejects_duplicate_content_for_same_asset(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    first = prepare_upload(session, asset)
    first_file = staging_directory(tmp_path, first.upload_id) / "samples.csv"
    first_file.parent.mkdir(parents=True)
    first_file.write_text("duplicate content")
    mark_upload_complete(tmp_path, first.upload_id)
    finalize_upload(session, tmp_path, first.upload_id, first.upload_token, actor=asset.owner)

    second = prepare_upload(session, asset)
    second_file = staging_directory(tmp_path, second.upload_id) / "copy.csv"
    second_file.parent.mkdir(parents=True)
    second_file.write_text("duplicate content")
    mark_upload_complete(tmp_path, second.upload_id)

    with pytest.raises(UploadContentError, match="内容相同"):
        finalize_upload(
            session,
            tmp_path,
            second.upload_id,
            second.upload_token,
            actor=asset.owner,
        )

    assert not (tmp_path / "dataset/soil-samples-2026/raw/2026-08/copy.csv").exists()
    assert second_file.exists()
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 1


def test_finalize_upload_preserves_uploaded_directory_structure(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset, recursive=True)
    staged_root = staging_directory(tmp_path, prepared.upload_id)
    (staged_root / "experiment-a/results").mkdir(parents=True)
    (staged_root / "experiment-a/README.md").write_text("experiment")
    (staged_root / "experiment-a/results/metrics.json").write_text('{"score": 1}')
    mark_upload_complete(tmp_path, prepared.upload_id)

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
    mark_upload_complete(tmp_path, prepared.upload_id)

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
    mark_upload_complete(tmp_path, prepared.upload_id)
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


def test_finalize_upload_rejects_complete_archive_paths_over_database_limit(
    tmp_path: Path,
) -> None:
    session = make_session()
    asset = create_asset(session)
    target_subdirectory = f"raw/{'t' * 200}/{'u' * 180}"
    prepared = prepare_upload(
        session,
        asset,
        target_subdirectory=target_subdirectory,
    )
    staged_file = staging_directory(tmp_path, prepared.upload_id).joinpath(
        "a" * 200,
        "b" * 200,
        f"{'c' * 200}.csv",
    )
    staged_file.parent.mkdir(parents=True)
    staged_file.write_text("too deep")
    mark_upload_complete(tmp_path, prepared.upload_id)

    with pytest.raises(UploadContentError, match="1000 个字符"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )

    complete_path = (
        Path("dataset")
        / asset.slug
        / target_subdirectory
        / staged_file.relative_to(staging_directory(tmp_path, prepared.upload_id))
    )
    assert len(complete_path.as_posix()) > 1000
    assert staged_file.exists()
    assert not (tmp_path / "dataset").exists()
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 0


def test_finalize_upload_rejects_source_replaced_with_symlink_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("approved")
    mark_upload_complete(tmp_path, prepared.upload_id)
    external = tmp_path / "outside.csv"
    external.write_text("outside secret")
    original_open = os.open
    replaced = False

    def replace_before_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if path == "samples.csv" and kwargs.get("dir_fd") is not None and not replaced:
            replaced = True
            staged.unlink()
            staged.symlink_to(external)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("app.services.transfers.os.open", replace_before_open)

    with pytest.raises(UploadContentError, match="路径在入库期间发生变化"):
        finalize_upload(
            session,
            tmp_path,
            prepared.upload_id,
            prepared.upload_token,
            actor=asset.owner,
        )

    assert not (tmp_path / "dataset").exists()
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 0


def test_finalize_upload_blocks_database_path_conflict(tmp_path: Path) -> None:
    session = make_session()
    asset = create_asset(session)
    prepared = prepare_upload(session, asset)
    staged = staging_directory(tmp_path, prepared.upload_id) / "samples.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("new")
    mark_upload_complete(tmp_path, prepared.upload_id)
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
    mark_upload_complete(tmp_path, prepared.upload_id)
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
    mark_upload_complete(tmp_path, prepared.upload_id)
    compensation_events: list[str] = []
    published = tmp_path / "dataset" / asset.slug / "raw" / "2026-08" / "samples.csv"
    original_flush = session.flush
    original_rollback = session.rollback
    original_unlink = os.unlink

    def fail_flush(*args, **kwargs):
        if published.exists():
            raise RuntimeError("database unavailable")
        return original_flush(*args, **kwargs)

    def record_rollback(*args, **kwargs):
        compensation_events.append("rollback")
        return original_rollback(*args, **kwargs)

    def record_unlink(*args, **kwargs):
        compensation_events.append("unlink")
        return original_unlink(*args, **kwargs)

    monkeypatch.setattr(session, "flush", fail_flush)
    monkeypatch.setattr(session, "rollback", record_rollback)
    monkeypatch.setattr("app.services.transfers.os.unlink", record_unlink)

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
    assert compensation_events[:2] == ["unlink", "rollback"]
    monkeypatch.setattr(session, "flush", original_flush)
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 0


def test_archive_scan_excludes_upload_staging_files(tmp_path: Path) -> None:
    session = make_session()
    create_asset(session)
    staged = staging_directory(tmp_path, uuid4()) / "partial.csv"
    staged.parent.mkdir(parents=True)
    staged.write_text("partial")
    lock_file = tmp_path / UPLOAD_LOCKS_DIRECTORY / f"{uuid4()}.lock"
    lock_file.parent.mkdir()
    lock_file.touch()

    result = scan_storage(session, tmp_path)
    session.commit()

    assert result.files_discovered == 0
    assert result.files_unclaimed == 0
    assert session.scalar(select(func.count()).select_from(FileRecord)) == 0
