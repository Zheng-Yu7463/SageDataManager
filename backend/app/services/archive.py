from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from mimetypes import guess_type
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import AssetType, HealthStatus
from app.domain.models import Asset, FileRecord, ScanRun, UnclaimedFile
from app.domain.schemas import ArchiveHealthSummary, ScanRunSummary
from app.services.storage import (
    MAX_ARCHIVE_RELATIVE_PATH_LENGTH,
    StorageIndexBusyError,
    StorageRootUnavailableError,
    file_kind,
    iter_storage_file_entries,
    storage_index_guard,
)
from app.services.unclaimed import (
    UnclaimedFileSnapshot,
    sync_unclaimed_file_snapshots,
)


class StorageScanError(Exception):
    pass


class ScanAlreadyRunningError(Exception):
    pass


@contextmanager
def _exclusive_scan(session: Session):
    try:
        with storage_index_guard(session, shared=False):
            yield
    except StorageIndexBusyError:
        raise ScanAlreadyRunningError from None


def _indexed_snapshot_matches(
    record: FileRecord,
    *,
    file_size: int,
    modified_at: datetime,
) -> bool:
    indexed_modified_at = record.modified_at
    if indexed_modified_at is None or record.file_size != file_size:
        return False
    if indexed_modified_at.tzinfo is None:
        indexed_modified_at = indexed_modified_at.replace(tzinfo=UTC)
    return indexed_modified_at.astimezone(UTC) == modified_at


def scan_run_summary(run: ScanRun) -> ScanRunSummary:
    return ScanRunSummary.model_validate(run)


def scan_storage(session: Session, storage_root: Path) -> ScanRunSummary:
    with _exclusive_scan(session):
        return _scan_storage(session, storage_root)


def _scan_storage(session: Session, storage_root: Path) -> ScanRunSummary:
    run = ScanRun(
        source="mock-archive" if storage_root.name == "sample-archive" else "storage-root"
    )
    session.add(run)
    session.flush()
    now = datetime.now(UTC)

    try:
        root = storage_root.resolve(strict=True)
    except OSError as error:
        run.status = "failed"
        run.message = "存储根不可用，未执行扫描。"
        run.completed_at = now
        session.flush()
        raise StorageScanError from error

    if not root.is_dir():
        run.status = "failed"
        run.message = "存储根不是目录，未执行扫描。"
        run.completed_at = now
        session.flush()
        raise StorageScanError

    asset_rows = session.execute(select(Asset.id, Asset.type, Asset.slug)).all()
    assets = {
        (asset_type.value, slug): asset_id for asset_id, asset_type, slug in asset_rows
    }
    asset_ids = {asset_id for asset_id, _, _ in asset_rows}
    claimed_assets = dict(
        session.execute(
            select(UnclaimedFile.relative_path, UnclaimedFile.claimed_asset_id).where(
                UnclaimedFile.claimed_asset_id.is_not(None)
            )
        ).all()
    )
    existing = {
        record.relative_path: record for record in session.scalars(select(FileRecord)).all()
    }
    seen_paths: set[str] = set()
    unclaimed_snapshots: list[UnclaimedFileSnapshot] = []

    def count_skipped_file() -> None:
        run.files_skipped += 1

    try:
        entries = iter_storage_file_entries(root, on_skip=count_skipped_file)
        for entry in entries:
            metadata = entry.metadata
            relative = entry.relative_path
            run.files_discovered += 1
            relative_path = relative.as_posix()
            if len(relative_path) > MAX_ARCHIVE_RELATIVE_PATH_LENGTH:
                run.files_skipped += 1
                continue
            file_name = relative.name
            candidate_kind = file_kind(Path(file_name))
            mime_type = guess_type(file_name)[0]
            modified_at = datetime.fromtimestamp(metadata.st_mtime, UTC)
            parts = relative.parts
            registered_asset_id = None
            if len(parts) >= 3:
                try:
                    asset_type = AssetType(parts[0])
                    registered_asset_id = assets.get((asset_type.value, parts[1]))
                except ValueError:
                    pass
            if registered_asset_id is None:
                unclaimed_snapshots.append(
                    UnclaimedFileSnapshot(
                        relative_path=relative_path,
                        file_name=file_name,
                        file_kind=candidate_kind,
                        mime_type=mime_type,
                        file_size=metadata.st_size,
                        modified_at=modified_at,
                    )
                )
            asset_id = registered_asset_id
            if asset_id is None:
                claimed_asset_id = claimed_assets.get(relative_path)
                if claimed_asset_id in asset_ids:
                    asset_id = claimed_asset_id
            if asset_id is None:
                run.files_unclaimed += 1
                continue

            record = existing.get(relative_path)
            if not record:
                record = FileRecord(asset_id=asset_id, relative_path=relative_path)
                session.add(record)
            else:
                if not _indexed_snapshot_matches(
                    record,
                    file_size=metadata.st_size,
                    modified_at=modified_at,
                ):
                    record.checksum = None
                record.asset_id = asset_id
            record.file_name = file_name
            record.file_kind = candidate_kind
            record.mime_type = mime_type
            record.file_size = metadata.st_size
            record.health_status = HealthStatus.HEALTHY
            record.modified_at = modified_at
            run.files_indexed += 1
            seen_paths.add(relative_path)
    except StorageRootUnavailableError as error:
        run.status = "failed"
        run.message = "存储根不可用，未执行扫描。"
        run.completed_at = datetime.now(UTC)
        session.flush()
        raise StorageScanError from error

    for relative_path, record in existing.items():
        if relative_path not in seen_paths:
            record.health_status = HealthStatus.MISSING
            run.files_missing += 1

    sync_unclaimed_file_snapshots(session, unclaimed_snapshots)
    run.status = "completed"
    run.message = "扫描完成；未匹配文件保留为待认领，不会自动创建资产。"
    run.completed_at = datetime.now(UTC)
    session.flush()
    return scan_run_summary(run)


def archive_health(session: Session, storage_root: Path) -> ArchiveHealthSummary:
    scans = session.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(8)).all()
    indexed, healthy, missing = session.execute(
        select(
            func.count(FileRecord.id),
            func.count(FileRecord.id).filter(FileRecord.health_status == HealthStatus.HEALTHY),
            func.count(FileRecord.id).filter(FileRecord.health_status == HealthStatus.MISSING),
        )
    ).one()
    unclaimed = session.scalar(
        select(func.count(UnclaimedFile.id)).where(UnclaimedFile.claimed_asset_id.is_(None))
    )
    return ArchiveHealthSummary(
        storage_available=storage_root.is_dir(),
        latest_scan=scan_run_summary(scans[0]) if scans else None,
        recent_scans=[scan_run_summary(scan) for scan in scans],
        indexed_files=indexed,
        healthy_files=healthy,
        missing_files=missing,
        unclaimed_files=unclaimed or 0,
    )
