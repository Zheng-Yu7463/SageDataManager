from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.activity import ActivityAction
from app.domain.enums import HealthStatus
from app.domain.models import Activity, Asset, FileRecord, User

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


@dataclass(frozen=True)
class ProtectedFileDelivery:
    path: Path
    content_disposition: str
    media_type: str


def can_preview(mime_type: str | None) -> bool:
    return mime_type in PREVIEW_MIME_TYPES or mime_type in PREVIEW_IMAGE_MIME_TYPES


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


def verify_file_access(session: Session, file_id: UUID, mode: str) -> None:
    record = _file_record(session, file_id)
    if mode == "preview" and not can_preview(record.mime_type):
        raise FilePreviewUnavailableError


def prepare_file_delivery(
    session: Session,
    storage_root: Path,
    file_id: UUID,
    mode: str,
    *,
    actor: User,
) -> ProtectedFileDelivery:
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
    try:
        root = storage_root.resolve(strict=True)
        resolved_path = root.joinpath(*relative_path.parts).resolve(strict=True)
        resolved_path.relative_to(root)
    except (OSError, ValueError):
        raise FileUnavailableError from None
    if not resolved_path.is_file():
        raise FileUnavailableError

    action = (
        ActivityAction.PREVIEWED_FILE
        if mode == "preview"
        else ActivityAction.DOWNLOADED_FILE
    )
    action_label = "预览" if mode == "preview" else "下载"
    session.add(
        Activity(
            asset_id=record.asset_id,
            actor=actor,
            action=action,
            description=f"{action_label}了文件 {record.file_name}",
        )
    )
    disposition = "inline" if mode == "preview" else "attachment"
    return ProtectedFileDelivery(
        path=resolved_path,
        content_disposition=disposition,
        media_type=record.mime_type or "application/octet-stream",
    )
