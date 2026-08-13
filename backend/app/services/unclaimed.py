from __future__ import annotations

from datetime import UTC, datetime
from mimetypes import guess_type
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.constraints import violates_constraint
from app.domain.activity import ActivityAction
from app.domain.enums import HealthStatus
from app.domain.models import Activity, Asset, FileRecord, UnclaimedFile, User
from app.domain.schemas import FileClaimResult, FileSummary, UnclaimedFileSummary


class UnclaimedFileNotFoundError(Exception):
    pass


class AssetNotFoundError(Exception):
    pass


class FileAlreadyClaimedError(Exception):
    pass


class ClaimSourceFileError(Exception):
    pass


class FilePathConflictError(Exception):
    pass


FILE_PATH_UNIQUE_CONSTRAINT = "uq_asset_files_relative_path"


def locked_unclaimed_file_statement(unclaimed_file_id: UUID):
    return select(UnclaimedFile).where(UnclaimedFile.id == unclaimed_file_id).with_for_update()


def file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".doc", ".docx", ".md", ".txt", ".tex"}:
        return "document"
    if suffix in {".csv", ".tsv", ".json", ".jsonl", ".parquet", ".nc"}:
        return "data"
    if suffix in {".pt", ".pth", ".bin", ".safetensors", ".ckpt"}:
        return "model-weight"
    if suffix in {".png", ".jpg", ".jpeg", ".svg", ".gif"}:
        return "image"
    return "other"


def sync_unclaimed_files(session: Session, storage_root: Path) -> None:
    assets = {
        (asset.type.value, asset.slug)
        for asset in session.scalars(select(Asset).where(Asset.archived_at.is_(None))).all()
    }
    existing = {
        record.relative_path: record for record in session.scalars(select(UnclaimedFile)).all()
    }
    root = storage_root.resolve()
    seen_paths: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        parts = relative.parts
        if len(parts) >= 3 and (parts[0], parts[1]) in assets:
            continue
        relative_path = relative.as_posix()
        seen_paths.add(relative_path)
        record = existing.get(relative_path)
        if not record:
            record = UnclaimedFile(relative_path=relative_path)
            session.add(record)
            existing[relative_path] = record
        stat = resolved.stat()
        record.file_name = resolved.name
        record.file_kind = file_kind(resolved)
        record.mime_type = guess_type(resolved.name)[0]
        record.file_size = stat.st_size
        record.modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
        record.last_seen_at = datetime.now(UTC)
    for relative_path, record in existing.items():
        if relative_path not in seen_paths and record.claimed_asset_id is None:
            session.delete(record)


def claim_unclaimed_file(
    session: Session,
    storage_root: Path,
    unclaimed_file_id: UUID,
    asset_id: UUID,
    *,
    actor: User | None = None,
) -> FileClaimResult:
    record = session.scalar(locked_unclaimed_file_statement(unclaimed_file_id))
    if not record:
        raise UnclaimedFileNotFoundError
    if record.claimed_asset_id:
        raise FileAlreadyClaimedError

    asset = session.get(Asset, asset_id)
    if not asset or asset.archived_at:
        raise AssetNotFoundError

    try:
        root = storage_root.resolve(strict=True)
        relative = Path(record.relative_path)
        if relative.is_absolute():
            raise ValueError
        resolved = (root / relative).resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError):
        raise ClaimSourceFileError from None
    if not resolved.is_file():
        raise ClaimSourceFileError

    file_record = session.scalar(
        select(FileRecord).where(FileRecord.relative_path == record.relative_path)
    )
    if file_record and file_record.asset_id != asset.id:
        raise FilePathConflictError
    if not file_record:
        file_record = FileRecord(asset_id=asset.id, relative_path=record.relative_path)
        session.add(file_record)

    stat = resolved.stat()
    file_record.file_name = resolved.name
    file_record.file_kind = file_kind(resolved)
    file_record.mime_type = guess_type(resolved.name)[0]
    file_record.file_size = stat.st_size
    file_record.health_status = HealthStatus.HEALTHY
    file_record.modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
    record.claimed_asset_id = asset.id
    record.claimed_at = datetime.now(UTC)
    if actor:
        session.add(
            Activity(
                asset=asset,
                actor=actor,
                action=ActivityAction.CLAIMED_FILE,
                description=f"认领了文件 {record.file_name}",
            )
        )

    try:
        session.flush()
    except IntegrityError as error:
        if violates_constraint(error, FILE_PATH_UNIQUE_CONSTRAINT):
            raise FilePathConflictError from error
        raise
    return FileClaimResult(asset_id=asset.id, file=FileSummary.model_validate(file_record))


def list_unclaimed_files(session: Session) -> list[UnclaimedFileSummary]:

    records = session.scalars(
        select(UnclaimedFile)
        .where(UnclaimedFile.claimed_asset_id.is_(None))
        .order_by(UnclaimedFile.last_seen_at.desc())
    ).all()
    return [UnclaimedFileSummary.model_validate(record) for record in records]
