from __future__ import annotations

from datetime import UTC, datetime
from mimetypes import guess_type
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Asset, UnclaimedFile
from app.domain.schemas import UnclaimedFileSummary


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


def list_unclaimed_files(session: Session) -> list[UnclaimedFileSummary]:
    records = session.scalars(
        select(UnclaimedFile).order_by(UnclaimedFile.last_seen_at.desc())
    ).all()
    return [UnclaimedFileSummary.model_validate(record) for record in records]
