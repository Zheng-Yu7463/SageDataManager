from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import stat_result
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.activity import ActivityAction
from app.domain.enums import HealthStatus
from app.domain.models import Asset, FileAccessGrant, FileRecord, User
from app.services.activities import record_activity

PREVIEW_MIME_TYPES = {
    "application/json",
    "application/pdf",
    "application/x-yaml",
    "text/csv",
    "text/markdown",
    "text/plain",
    "text/tab-separated-values",
    "text/yaml",
}
PREVIEW_IMAGE_MIME_TYPES = {"image/avif", "image/gif", "image/jpeg", "image/png", "image/webp"}


class FileAccessError(Exception):
    pass


class FileNotFoundError(FileAccessError):
    pass


class FileUnavailableError(FileAccessError):
    pass


class FilePreviewUnavailableError(FileAccessError):
    pass


class FileAccessGrantInvalidError(FileAccessError):
    pass


@dataclass
class OpenedFileDelivery:
    descriptor: int
    file_name: str
    stat: stat_result
    content_disposition: str
    media_type: str

    def close(self) -> None:
        if self.descriptor < 0:
            return
        descriptor = self.descriptor
        self.descriptor = -1
        os.close(descriptor)

    def take_descriptor(self) -> int:
        if self.descriptor < 0:
            raise RuntimeError("File delivery descriptor has already been released.")
        descriptor = self.descriptor
        self.descriptor = -1
        return descriptor


def can_preview(mime_type: str | None) -> bool:
    return mime_type in PREVIEW_MIME_TYPES or mime_type in PREVIEW_IMAGE_MIME_TYPES


def _matches_indexed_snapshot(record: FileRecord, current: stat_result) -> bool:
    if record.modified_at is None or record.file_size != current.st_size:
        return False
    indexed_modified_at = record.modified_at
    if indexed_modified_at.tzinfo is None:
        indexed_modified_at = indexed_modified_at.replace(tzinfo=UTC)
    return indexed_modified_at.astimezone(UTC) == datetime.fromtimestamp(current.st_mtime, UTC)


def _file_record(session: Session, file_id: UUID) -> FileRecord:
    record = session.scalar(
        select(FileRecord)
        .join(FileRecord.asset)
        .where(FileRecord.id == file_id, Asset.archived_at.is_(None))
    )
    if not record:
        raise FileNotFoundError
    if record.health_status != HealthStatus.HEALTHY:
        raise FileUnavailableError
    return record


def _open_indexed_file(storage_root: Path, relative_path: PurePosixPath) -> tuple[int, stat_result]:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC

    try:
        trusted_root = storage_root.resolve(strict=True)
        parent_descriptor = os.open(trusted_root, directory_flags)
    except OSError:
        raise FileUnavailableError from None

    try:
        for directory_name in relative_path.parts[:-1]:
            try:
                child_descriptor = os.open(
                    directory_name,
                    directory_flags,
                    dir_fd=parent_descriptor,
                )
            except OSError:
                raise FileUnavailableError from None
            os.close(parent_descriptor)
            parent_descriptor = child_descriptor

        try:
            descriptor = os.open(
                relative_path.parts[-1],
                file_flags,
                dir_fd=parent_descriptor,
            )
        except OSError:
            raise FileUnavailableError from None
        try:
            current = os.fstat(descriptor)
        except OSError:
            os.close(descriptor)
            raise FileUnavailableError from None
        if not stat.S_ISREG(current.st_mode):
            os.close(descriptor)
            raise FileUnavailableError
        return descriptor, current
    finally:
        os.close(parent_descriptor)


def verify_file_access(session: Session, file_id: UUID, mode: str) -> None:
    record = _file_record(session, file_id)
    if mode == "preview" and not can_preview(record.mime_type):
        raise FilePreviewUnavailableError


def issue_file_access_grant(
    session: Session,
    file_id: UUID,
    mode: str,
    *,
    actor: User,
    ttl_seconds: int,
) -> FileAccessGrant:
    verify_file_access(session, file_id, mode)
    now = datetime.now(UTC)
    session.execute(delete(FileAccessGrant).where(FileAccessGrant.expires_at <= now))
    grant = FileAccessGrant(
        file_id=file_id,
        user_id=actor.id,
        mode=mode,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    session.add(grant)
    session.flush()
    return grant


def authorize_file_access_grant(
    session: Session,
    grant_id: UUID,
    file_id: UUID,
    *,
    record_access: bool = True,
) -> tuple[User, str, bool]:
    first_access = None
    if record_access:
        first_access = session.execute(
            update(FileAccessGrant)
            .execution_options(synchronize_session=False)
            .where(
                FileAccessGrant.id == grant_id,
                FileAccessGrant.file_id == file_id,
                FileAccessGrant.expires_at > datetime.now(UTC),
                FileAccessGrant.first_accessed_at.is_(None),
            )
            .values(first_accessed_at=datetime.now(UTC))
            .returning(FileAccessGrant.user_id, FileAccessGrant.mode)
        ).one_or_none()
    grant = (
        first_access
        or session.execute(
            select(FileAccessGrant.user_id, FileAccessGrant.mode).where(
                FileAccessGrant.id == grant_id,
                FileAccessGrant.file_id == file_id,
                FileAccessGrant.expires_at > datetime.now(UTC),
            )
        ).one_or_none()
    )
    if not grant:
        raise FileAccessGrantInvalidError
    actor = session.get(User, grant.user_id)
    if not actor or not actor.is_active or actor.role != "admin":
        raise FileAccessGrantInvalidError
    return actor, grant.mode, first_access is not None


def open_file_delivery(
    session: Session,
    storage_root: Path,
    file_id: UUID,
    mode: str,
    *,
    actor: User,
    credential_name: str | None = None,
    audit_access: bool = True,
) -> OpenedFileDelivery:
    record = _file_record(session, file_id)
    if mode == "preview" and not can_preview(record.mime_type):
        raise FilePreviewUnavailableError

    relative_path = PurePosixPath(record.relative_path)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {".", ".."} for part in relative_path.parts)
    ):
        raise FileUnavailableError
    descriptor, current = _open_indexed_file(storage_root, relative_path)
    try:
        if not _matches_indexed_snapshot(record, current):
            raise FileUnavailableError
        if audit_access:
            action = (
                ActivityAction.PREVIEWED_FILE
                if mode == "preview"
                else ActivityAction.DOWNLOADED_FILE
            )
            action_label = "预览" if mode == "preview" else "下载"
            asset = session.get(Asset, record.asset_id)
            if not asset:
                raise FileUnavailableError
            record_activity(
                session,
                asset=asset,
                actor=actor,
                credential_name=credential_name,
                action=action,
                description=f"{action_label}了文件 {record.file_name}",
            )
    except Exception:
        os.close(descriptor)
        raise
    disposition = "inline" if mode == "preview" else "attachment"
    return OpenedFileDelivery(
        descriptor=descriptor,
        file_name=record.file_name,
        stat=current,
        content_disposition=disposition,
        media_type=record.mime_type or "application/octet-stream",
    )
