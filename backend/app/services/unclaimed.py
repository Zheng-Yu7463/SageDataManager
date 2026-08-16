from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from mimetypes import guess_type
from os import stat_result
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.constraints import violates_constraint
from app.domain.activity import ActivityAction
from app.domain.enums import HealthStatus
from app.domain.models import Asset, FileRecord, UnclaimedFile, User
from app.domain.schemas import FileClaimResult, FileSummary, UnclaimedFileSummary
from app.services.activities import record_activity
from app.services.storage import (
    MAX_ARCHIVE_RELATIVE_PATH_LENGTH,
    file_kind,
    is_internal_storage_path,
)


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

@dataclass(frozen=True)
class UnclaimedFileSnapshot:
    relative_path: str
    file_name: str
    file_kind: str
    mime_type: str | None
    file_size: int
    modified_at: datetime


def locked_unclaimed_file_statement(unclaimed_file_id: UUID):
    return select(UnclaimedFile).where(UnclaimedFile.id == unclaimed_file_id).with_for_update()


def locked_claim_asset_statement(asset_id: UUID):
    return (
        select(Asset)
        .where(Asset.id == asset_id, Asset.archived_at.is_(None))
        .with_for_update()
    )


def locked_unclaimed_files_statement():
    return select(UnclaimedFile).with_for_update()



@dataclass
class OpenedUnclaimedSource:
    relative_path: PurePosixPath
    descriptor: int
    parent_descriptor: int
    metadata: stat_result

    def close(self) -> None:
        descriptors = (self.descriptor, self.parent_descriptor)
        self.descriptor = -1
        self.parent_descriptor = -1
        for descriptor in descriptors:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)


def _open_unclaimed_source(storage_root: Path, relative_path: str) -> OpenedUnclaimedSource:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.as_posix() != relative_path
        or any(part in {".", ".."} for part in relative.parts)
        or is_internal_storage_path(relative)
    ):
        raise ClaimSourceFileError

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    parent_descriptor = -1
    descriptor = -1
    try:
        root = storage_root.resolve(strict=True)
        parent_descriptor = os.open(root, directory_flags)
        for directory_name in relative.parts[:-1]:
            next_descriptor = os.open(
                directory_name,
                directory_flags,
                dir_fd=parent_descriptor,
            )
            previous_descriptor = parent_descriptor
            parent_descriptor = next_descriptor
            os.close(previous_descriptor)
        descriptor = os.open(
            relative.name,
            file_flags,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError
        source = OpenedUnclaimedSource(
            relative_path=relative,
            descriptor=descriptor,
            parent_descriptor=parent_descriptor,
            metadata=metadata,
        )
        descriptor = -1
        parent_descriptor = -1
        return source
    except (OSError, ValueError):
        raise ClaimSourceFileError from None
    finally:
        for opened_descriptor in (descriptor, parent_descriptor):
            if opened_descriptor >= 0:
                with suppress(OSError):
                    os.close(opened_descriptor)


def _matches_unclaimed_snapshot(record: UnclaimedFile, metadata: stat_result) -> bool:
    if record.modified_at is None or record.file_size != metadata.st_size:
        return False
    expected_modified_at = record.modified_at
    if expected_modified_at.tzinfo is None:
        expected_modified_at = expected_modified_at.replace(tzinfo=UTC)
    return expected_modified_at.astimezone(UTC) == datetime.fromtimestamp(
        metadata.st_mtime, UTC
    )


def _ensure_unclaimed_source_unchanged(source: OpenedUnclaimedSource) -> None:
    try:
        current = os.fstat(source.descriptor)
        linked = os.stat(
            source.relative_path.name,
            dir_fd=source.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise ClaimSourceFileError from None
    opened_identity = (source.metadata.st_dev, source.metadata.st_ino)
    stable_fields = (
        source.metadata.st_size,
        source.metadata.st_mtime_ns,
        source.metadata.st_ctime_ns,
    )
    if (
        not stat.S_ISREG(linked.st_mode)
        or opened_identity != (current.st_dev, current.st_ino)
        or opened_identity != (linked.st_dev, linked.st_ino)
        or stable_fields
        != (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
    ):
        raise ClaimSourceFileError


def sync_unclaimed_file_snapshots(
    session: Session,
    snapshots: Iterable[UnclaimedFileSnapshot],
) -> None:
    existing = {
        record.relative_path: record
        for record in session.scalars(locked_unclaimed_files_statement()).all()
    }
    seen_paths: set[str] = set()
    last_seen_at = datetime.now(UTC)
    for snapshot in snapshots:
        seen_paths.add(snapshot.relative_path)
        record = existing.get(snapshot.relative_path)
        if not record:
            record = UnclaimedFile(relative_path=snapshot.relative_path)
            session.add(record)
            existing[snapshot.relative_path] = record
        record.file_name = snapshot.file_name
        record.file_kind = snapshot.file_kind
        record.mime_type = snapshot.mime_type
        record.file_size = snapshot.file_size
        record.modified_at = snapshot.modified_at
        record.last_seen_at = last_seen_at
    for relative_path, record in existing.items():
        if relative_path not in seen_paths and record.claimed_asset_id is None:
            session.delete(record)


def sync_unclaimed_files(session: Session, storage_root: Path) -> None:
    assets = {
        (asset_type.value, slug)
        for asset_type, slug in session.execute(select(Asset.type, Asset.slug))
    }
    root = storage_root.resolve()
    snapshots: list[UnclaimedFileSnapshot] = []
    for candidate in root.rglob("*"):
        try:
            if is_internal_storage_path(candidate.relative_to(root)):
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root)
            metadata = resolved.stat()
        except (OSError, ValueError):
            continue
        parts = relative.parts
        if len(parts) >= 3 and (parts[0], parts[1]) in assets:
            continue
        relative_path = relative.as_posix()
        if len(relative_path) > MAX_ARCHIVE_RELATIVE_PATH_LENGTH:
            continue
        snapshots.append(
            UnclaimedFileSnapshot(
                relative_path=relative_path,
                file_name=resolved.name,
                file_kind=file_kind(resolved),
                mime_type=guess_type(resolved.name)[0],
                file_size=metadata.st_size,
                modified_at=datetime.fromtimestamp(metadata.st_mtime, UTC),
            )
        )
    sync_unclaimed_file_snapshots(session, snapshots)


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

    asset = session.scalar(locked_claim_asset_statement(asset_id))
    if not asset:
        raise AssetNotFoundError

    source = _open_unclaimed_source(storage_root, record.relative_path)
    try:
        if not _matches_unclaimed_snapshot(record, source.metadata):
            raise ClaimSourceFileError
        file_record = session.scalar(
            select(FileRecord).where(FileRecord.relative_path == record.relative_path)
        )
        if file_record and file_record.asset_id != asset.id:
            raise FilePathConflictError
        if not file_record:
            file_record = FileRecord(asset_id=asset.id, relative_path=record.relative_path)
            session.add(file_record)
        file_record.file_name = source.relative_path.name
        file_record.file_kind = file_kind(Path(source.relative_path.name))
        file_record.mime_type = guess_type(source.relative_path.name)[0]
        file_record.file_size = source.metadata.st_size
        file_record.health_status = HealthStatus.HEALTHY
        file_record.modified_at = datetime.fromtimestamp(source.metadata.st_mtime, UTC)
        record.claimed_asset_id = asset.id
        record.claimed_at = datetime.now(UTC)
        if actor:
            record_activity(
                session,
                asset=asset,
                actor=actor,
                action=ActivityAction.CLAIMED_FILE,
                description=f"认领了文件 {record.file_name}",
            )

        _ensure_unclaimed_source_unchanged(source)
        try:
            session.flush()
        except IntegrityError as error:
            if violates_constraint(error, FILE_PATH_UNIQUE_CONSTRAINT):
                raise FilePathConflictError from error
            raise
        _ensure_unclaimed_source_unchanged(source)
        return FileClaimResult(
            asset_id=asset.id, file=FileSummary.model_validate(file_record)
        )
    finally:
        source.close()


def list_unclaimed_files(
    session: Session,
    *,
    page: int,
    page_size: int,
) -> tuple[list[UnclaimedFileSummary], int]:
    filters = (UnclaimedFile.claimed_asset_id.is_(None),)
    total = session.scalar(
        select(func.count()).select_from(UnclaimedFile).where(*filters)
    ) or 0
    records = session.scalars(
        select(UnclaimedFile)
        .where(*filters)
        .order_by(UnclaimedFile.last_seen_at.desc(), UnclaimedFile.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [UnclaimedFileSummary.model_validate(record) for record in records], total
