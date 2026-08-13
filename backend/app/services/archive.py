from __future__ import annotations

from datetime import UTC, datetime
from mimetypes import guess_type
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import AssetType, HealthStatus
from app.domain.models import Asset, FileRecord, ScanRun, UnclaimedFile
from app.domain.schemas import ArchiveHealthSummary, ScanRunSummary
from app.services.storage import file_kind, is_internal_storage_path
from app.services.unclaimed import sync_unclaimed_files


class StorageScanError(Exception):
    pass


def scan_run_summary(run: ScanRun) -> ScanRunSummary:
    return ScanRunSummary.model_validate(run)


def scan_storage(session: Session, storage_root: Path) -> ScanRunSummary:
    run = ScanRun(
        source="mock-archive" if storage_root.name == "sample-archive" else "storage-root"
    )
    session.add(run)
    session.flush()
    now = datetime.now(UTC)

    try:
        root = storage_root.resolve(strict=True)
    except FileNotFoundError as error:
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

    active_assets = session.scalars(select(Asset).where(Asset.archived_at.is_(None))).all()
    assets = {(asset.type.value, asset.slug): asset for asset in active_assets}
    assets_by_id = {asset.id: asset for asset in active_assets}
    claimed_assets = {
        record.relative_path: record.claimed_asset_id
        for record in session.scalars(
            select(UnclaimedFile).where(UnclaimedFile.claimed_asset_id.is_not(None))
        ).all()
    }
    existing = {
        record.relative_path: record
        for record in session.scalars(select(FileRecord)).all()
    }
    seen_paths: set[str] = set()

    for candidate in root.rglob("*"):
        if is_internal_storage_path(candidate.relative_to(root)):
            continue
        if candidate.is_symlink() or not candidate.is_file():
            if candidate.is_symlink():
                run.files_skipped += 1
            continue
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            run.files_skipped += 1
            continue

        run.files_discovered += 1
        relative_path = relative.as_posix()
        parts = relative.parts
        asset = None
        if len(parts) >= 3:
            try:
                asset_type = AssetType(parts[0])
                asset = assets.get((asset_type.value, parts[1]))
            except ValueError:
                pass
        if not asset:
            asset = assets_by_id.get(claimed_assets.get(relative_path))
        if not asset:
            run.files_unclaimed += 1
            continue

        stat = resolved.stat()
        record = existing.get(relative_path)
        if not record:
            record = FileRecord(asset_id=asset.id, relative_path=relative_path)
            session.add(record)
        else:
            record.asset_id = asset.id
        record.file_name = resolved.name
        record.file_kind = file_kind(resolved)
        record.mime_type = guess_type(resolved.name)[0]
        record.file_size = stat.st_size
        record.health_status = HealthStatus.HEALTHY
        record.modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
        run.files_indexed += 1
        seen_paths.add(relative_path)

    for relative_path, record in existing.items():
        if relative_path not in seen_paths:
            record.health_status = HealthStatus.MISSING
            run.files_missing += 1

    sync_unclaimed_files(session, root)
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
