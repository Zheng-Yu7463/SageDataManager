from __future__ import annotations

import os
from datetime import UTC, datetime
from mimetypes import guess_type
from pathlib import Path, PurePosixPath
from shlex import quote
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.activity import ActivityAction
from app.domain.enums import HealthStatus
from app.domain.models import Activity, Asset, FileRecord, User
from app.domain.schemas import (
    AgentUploadCreateResponse,
    AgentUploadedFileResponse,
    UploadCommandRequest,
    UploadCommandResponse,
    UploadFinalizeResponse,
)
from app.services.security import create_upload_token, read_upload_token
from app.services.storage import UPLOAD_PARTS_DIRECTORY, UPLOAD_STAGING_DIRECTORY, file_kind
from app.services.upload_directories import upload_directory_names


class UploadError(Exception):
    pass


class UploadCommandError(UploadError):
    pass


class UploadTicketError(UploadError):
    pass


class UploadNotReadyError(UploadError):
    pass


class UploadContentError(UploadError):
    pass


class UploadConflictError(UploadError):
    def __init__(self, paths: list[str]) -> None:
        self.paths = paths
        visible_paths = "\n".join(f"- {path}" for path in paths[:8])
        hidden_count = len(paths) - 8
        suffix = f"\n另有 {hidden_count} 个冲突路径" if hidden_count > 0 else ""
        super().__init__(f"以下归档路径已存在，请先处理冲突：\n{visible_paths}{suffix}")


def _validated_subdirectory(asset: Asset, value: str) -> PurePosixPath:
    subdirectory = PurePosixPath(value.strip())
    if (
        subdirectory.is_absolute()
        or not subdirectory.parts
        or any(part in {".", ".."} for part in subdirectory.parts)
    ):
        raise UploadCommandError("目标子目录必须是归档目录内的相对路径。")
    allowed_subdirectories = upload_directory_names(asset.type)
    if subdirectory.parts[0] not in allowed_subdirectories:
        allowed_names = "、".join(sorted(allowed_subdirectories))
        raise UploadCommandError(f"{asset.type.value} 资产的一级归档目录必须是：{allowed_names}。")
    return subdirectory


def generate_upload_command(
    session: Session,
    payload: UploadCommandRequest,
    *,
    ssh_host: str,
    ssh_user: str,
    ssh_port: int,
    destination_root: str,
    actor: User | None = None,
) -> UploadCommandResponse:
    asset = session.get(Asset, payload.asset_id)
    if not asset or asset.archived_at:
        raise UploadCommandError("目标资产不存在或已归档。")
    if not ssh_host.strip() or not ssh_user.strip() or not destination_root.strip() or ssh_port < 1:
        raise UploadCommandError("SCP 上传配置不完整。")
    source_path = payload.source_path.strip()
    if not source_path:
        raise UploadCommandError("本机待上传路径不能为空。")

    subdirectory = _validated_subdirectory(asset, payload.target_subdirectory)
    root = PurePosixPath(destination_root.strip())
    if not root.is_absolute():
        raise UploadCommandError("SCP 目标根目录必须使用绝对路径。")

    upload_id = uuid4()
    staging_relative_path = (
        PurePosixPath(UPLOAD_STAGING_DIRECTORY) / str(upload_id)
    ).as_posix()
    staging_destination = root / staging_relative_path
    archive_relative_path = (
        PurePosixPath(asset.type.value) / asset.slug / subdirectory
    ).as_posix()
    remote_login = f"{ssh_user.strip()}@{ssh_host.strip()}"
    remote_mkdir = f"mkdir -p -- {quote(staging_destination.as_posix())}"
    recursive = "-r " if payload.recursive else ""
    command = (
        f"ssh -p {ssh_port} {quote(remote_login)} {quote(remote_mkdir)} && "
        f"scp -P {ssh_port} {recursive}-- {quote(source_path)} "
        f"{quote(f'{remote_login}:{staging_destination.as_posix()}/')}"
    )
    upload_token, expires_at = create_upload_token(
        upload_id, asset.id, subdirectory.as_posix(), ssh_user.strip()
    )
    if actor:
        session.add(
            Activity(
                asset=asset,
                actor=actor,
                action=ActivityAction.PREPARED_UPLOAD,
                description=f"为 {archive_relative_path} 生成了上传指令",
            )
        )
    return UploadCommandResponse(
        upload_id=upload_id,
        asset_id=asset.id,
        asset_title=asset.title,
        archive_relative_path=archive_relative_path,
        staging_relative_path=staging_relative_path,
        upload_token=upload_token,
        expires_at=expires_at,
        command=command,
    )


def create_agent_upload(
    session: Session,
    asset_id: UUID,
    target_subdirectory: str,
    *,
    actor: User,
    credential_name: str,
) -> AgentUploadCreateResponse:
    asset = session.get(Asset, asset_id)
    if not asset or asset.archived_at:
        raise UploadCommandError("目标资产不存在或已归档。")
    subdirectory = _validated_subdirectory(asset, target_subdirectory)
    upload_id = uuid4()
    archive_relative_path = (
        PurePosixPath(asset.type.value) / asset.slug / subdirectory
    ).as_posix()
    upload_token, expires_at = create_upload_token(
        upload_id, asset.id, subdirectory.as_posix(), actor.username or ""
    )
    session.add(
        Activity(
            asset=asset,
            actor=actor,
            credential_name=credential_name,
            action=ActivityAction.PREPARED_UPLOAD,
            description=f"为 {archive_relative_path} 创建了 AI 上传任务",
        )
    )
    session.flush()
    return AgentUploadCreateResponse(
        upload_id=upload_id,
        asset_id=asset.id,
        asset_title=asset.title,
        archive_relative_path=archive_relative_path,
        upload_token=upload_token,
        expires_at=expires_at,
        file_upload_url_template=f"/api/agent/uploads/{upload_id}/files/{{relative_path}}",
        finalize_url=f"/api/agent/uploads/{upload_id}/finalize",
    )


def validate_agent_upload(
    upload_id: UUID,
    upload_token: str,
    actor: User,
) -> None:
    claims = read_upload_token(upload_token)
    if (
        not claims
        or claims.upload_id != upload_id
        or claims.username != (actor.username or "")
    ):
        raise UploadTicketError("上传凭据无效或已过期，请重新创建上传任务。")


def staged_upload_destination(
    storage_root: Path,
    upload_id: UUID,
    relative_path: str,
) -> tuple[Path, Path]:
    if len(relative_path) > 1000 or "\x00" in relative_path:
        raise UploadContentError("上传文件路径过长或包含无效字符。")
    upload_path = PurePosixPath(relative_path.strip())
    if (
        upload_path.is_absolute()
        or not upload_path.parts
        or any(part in {"", ".", ".."} for part in upload_path.parts)
        or any(len(part) > 255 for part in upload_path.parts)
    ):
        raise UploadContentError("上传文件路径必须是任务内的安全相对路径。")
    root = storage_root.resolve(strict=True)
    if not root.is_dir():
        raise UploadContentError("存储根不可用，无法接收文件。")
    staging_root = root / UPLOAD_STAGING_DIRECTORY
    if staging_root.is_symlink():
        raise UploadContentError("上传临时区不是有效目录，无法接收文件。")
    destination = staging_root.joinpath(str(upload_id), *upload_path.parts)
    if destination.exists() or destination.is_symlink():
        raise UploadConflictError([upload_path.as_posix()])
    if _unsafe_destination_parent(destination, root):
        raise UploadContentError("上传文件的父目录不可用。")
    return destination, staging_root / UPLOAD_PARTS_DIRECTORY


def complete_agent_file_upload(
    upload_id: UUID,
    relative_path: str,
    temporary_file: Path,
    destination: Path,
) -> AgentUploadedFileResponse:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise UploadConflictError([relative_path])
    try:
        os.link(temporary_file, destination)
    except FileExistsError:
        raise UploadConflictError([relative_path]) from None
    temporary_file.unlink()
    return AgentUploadedFileResponse(
        upload_id=upload_id,
        relative_path=PurePosixPath(relative_path).as_posix(),
        file_size=destination.stat().st_size,
    )


def _staged_files(staging_directory: Path) -> list[Path]:
    if not staging_directory.exists():
        raise UploadNotReadyError("尚未检测到文件，请确认终端传输已完成后重试。")
    if staging_directory.is_symlink() or not staging_directory.is_dir():
        raise UploadContentError("上传临时区不是有效目录，无法入库。")

    files: list[Path] = []
    for candidate in staging_directory.rglob("*"):
        if candidate.is_symlink():
            raise UploadContentError("上传内容含有符号链接，无法入库。")
        if candidate.is_file():
            files.append(candidate)
        elif not candidate.is_dir():
            raise UploadContentError("上传内容含有不支持的文件类型，无法入库。")
    if not files:
        raise UploadNotReadyError("尚未检测到文件，请确认终端传输已完成后重试。")
    return sorted(files, key=lambda path: path.relative_to(staging_directory).as_posix())


def _unsafe_destination_parent(destination: Path, storage_root: Path) -> bool:
    parent = storage_root
    for part in destination.relative_to(storage_root).parts[:-1]:
        parent /= part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            return True
    return False


def _remove_empty_staging_directories(staging_directory: Path, staging_root: Path) -> None:
    directories = [path for path in staging_directory.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        directory.rmdir()
    staging_directory.rmdir()
    if staging_root.exists() and not any(staging_root.iterdir()):
        staging_root.rmdir()


def _remove_empty_archive_directories(directories: set[Path], storage_root: Path) -> None:
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        if directory == storage_root or not directory.exists():
            continue
        try:
            directory.rmdir()
        except OSError:
            continue


def _restore_staging_directory(
    staging_directory: Path,
    moved_files: list[tuple[Path, Path]],
) -> None:
    staging_directory.mkdir(parents=True, exist_ok=True)
    for source, destination in reversed(moved_files):
        source.parent.mkdir(parents=True, exist_ok=True)
        destination.replace(source)


def finalize_upload(
    session: Session,
    storage_root: Path,
    upload_id: UUID,
    upload_token: str,
    *,
    actor: User,
    credential_name: str | None = None,
) -> UploadFinalizeResponse:
    claims = read_upload_token(upload_token)
    if (
        not claims
        or claims.upload_id != upload_id
        or claims.username != (actor.username or "")
    ):
        raise UploadTicketError("上传凭据无效或已过期，请重新生成上传命令。")

    asset = session.get(Asset, claims.asset_id)
    if not asset or asset.archived_at:
        raise UploadTicketError("目标资产不存在或已归档，请重新生成上传命令。")
    subdirectory = _validated_subdirectory(asset, claims.target_subdirectory)
    try:
        root = storage_root.resolve(strict=True)
    except FileNotFoundError as error:
        raise UploadContentError("存储根不可用，无法完成入库。") from error
    if not root.is_dir():
        raise UploadContentError("存储根不可用，无法完成入库。")

    staging_root = root / UPLOAD_STAGING_DIRECTORY
    staging_directory = staging_root / str(upload_id)
    if staging_root.is_symlink():
        raise UploadContentError("上传临时区不是有效目录，无法入库。")
    staged_files = _staged_files(staging_directory)
    archive_directory = root.joinpath(asset.type.value, asset.slug, *subdirectory.parts)
    destinations = [
        archive_directory / source.relative_to(staging_directory) for source in staged_files
    ]
    relative_paths = [destination.relative_to(root).as_posix() for destination in destinations]

    database_conflicts = set(
        session.scalars(
            select(FileRecord.relative_path).where(FileRecord.relative_path.in_(relative_paths))
        ).all()
    )
    conflicts = sorted(
        relative_path
        for destination, relative_path in zip(destinations, relative_paths, strict=True)
        if (
            destination.exists()
            or destination.is_symlink()
            or _unsafe_destination_parent(destination, root)
            or relative_path in database_conflicts
        )
    )
    if conflicts:
        raise UploadConflictError(conflicts)

    moved_files: list[tuple[Path, Path]] = []
    created_archive_directories: set[Path] = set()
    total_size = 0
    try:
        for source, destination in zip(staged_files, destinations, strict=True):
            missing_directories = []
            parent = destination.parent
            while parent != root and not parent.exists():
                missing_directories.append(parent)
                parent = parent.parent
            destination.parent.mkdir(parents=True, exist_ok=True)
            created_archive_directories.update(missing_directories)
            source.replace(destination)
            moved_files.append((source, destination))

        for destination, relative_path in zip(destinations, relative_paths, strict=True):
            stat = destination.stat()
            total_size += stat.st_size
            session.add(
                FileRecord(
                    asset=asset,
                    relative_path=relative_path,
                    file_name=destination.name,
                    file_kind=file_kind(destination),
                    mime_type=guess_type(destination.name)[0],
                    file_size=stat.st_size,
                    health_status=HealthStatus.HEALTHY,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                )
            )
        session.add(
            Activity(
                asset=asset,
                actor=actor,
                credential_name=credential_name,
                action=ActivityAction.COMPLETED_UPLOAD,
                description=(
                    f"向 {asset.type.value}/{asset.slug}/{subdirectory.as_posix()} "
                    f"入库了 {len(destinations)} 个文件"
                ),
            )
        )
        session.flush()
        _remove_empty_staging_directories(staging_directory, staging_root)
        session.commit()
    except Exception:
        session.rollback()
        _restore_staging_directory(staging_directory, moved_files)
        _remove_empty_archive_directories(created_archive_directories, root)
        raise

    return UploadFinalizeResponse(
        asset_id=asset.id,
        imported_file_count=len(destinations),
        total_size=total_size,
        relative_paths=relative_paths,
    )
