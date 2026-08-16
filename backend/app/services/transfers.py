from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from mimetypes import guess_type
from pathlib import Path, PurePosixPath
from shlex import quote
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.activity import ActivityAction
from app.domain.enums import HealthStatus
from app.domain.models import Asset, FileRecord, PersonalAccessToken, UploadTask, User
from app.domain.schemas import (
    AgentUploadCancelResponse,
    AgentUploadCreateResponse,
    AgentUploadedFileResponse,
    AgentUploadFileStatus,
    AgentUploadStatusResponse,
    UploadCommandRequest,
    UploadCommandResponse,
    UploadFinalizeResponse,
    UploadStatusResponse,
)
from app.services.activities import record_activity
from app.services.security import create_upload_token, read_upload_token
from app.services.storage import (
    MAX_ARCHIVE_RELATIVE_PATH_LENGTH,
    UPLOAD_LOCKS_DIRECTORY,
    UPLOAD_PARTS_DIRECTORY,
    UPLOAD_STAGING_DIRECTORY,
    StorageIndexBusyError,
    file_kind,
    storage_index_guard,
)
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


class UploadBusyError(UploadContentError):
    pass


class UploadTooLargeError(UploadContentError):
    pass


class UploadConflictError(UploadError):
    def __init__(self, paths: list[str]) -> None:
        self.paths = paths
        visible_paths = "\n".join(f"- {path}" for path in paths[:8])
        hidden_count = len(paths) - 8
        suffix = f"\n另有 {hidden_count} 个冲突路径" if hidden_count > 0 else ""
        super().__init__(f"以下归档路径已存在，请先处理冲突：\n{visible_paths}{suffix}")


@contextmanager
def upload_task_guard(storage_root: Path, upload_id: UUID) -> Iterator[None]:
    root_descriptor: int | None = None
    lock_directory_descriptor: int | None = None
    lock_descriptor: int | None = None
    locked = False
    try:
        try:
            root = storage_root.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError) as error:
            raise UploadContentError("存储根不可用，无法处理上传任务。") from error
        if not root.is_dir():
            raise UploadContentError("存储根不可用，无法处理上传任务。")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        root_descriptor = os.open(root, directory_flags)
        with suppress(FileExistsError):
            os.mkdir(UPLOAD_LOCKS_DIRECTORY, mode=0o700, dir_fd=root_descriptor)
        lock_directory_descriptor = os.open(
            UPLOAD_LOCKS_DIRECTORY,
            directory_flags,
            dir_fd=root_descriptor,
        )
        lock_descriptor = os.open(
            f"{upload_id}.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=lock_directory_descriptor,
        )
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UploadBusyError("上传任务正在处理，请检查任务状态后重试。") from None
        locked = True
        yield
    except UploadError:
        raise
    except OSError as error:
        raise UploadContentError("上传任务锁不可用，请检查存储根权限。") from error
    finally:
        if locked and lock_descriptor is not None:
            with suppress(OSError):
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        for descriptor in (
            lock_descriptor,
            lock_directory_descriptor,
            root_descriptor,
        ):
            if descriptor is not None:
                os.close(descriptor)


UPLOAD_COMPLETION_MARKER = ".sage-upload-complete"


@dataclass
class OpenStagedFile:
    relative_path: PurePosixPath
    descriptor: int
    parent_descriptor: int
    name: str
    metadata: os.stat_result


@dataclass
class PublishedFile:
    path: Path
    parent_descriptor: int
    checksum_sha256: str


def _new_upload_task(
    session: Session,
    *,
    upload_id: UUID,
    asset: Asset,
    actor: User,
    access_token: PersonalAccessToken | None,
    subdirectory: PurePosixPath,
    expires_at: datetime,
    transfer_mode: str,
) -> UploadTask:
    task = UploadTask(
        id=upload_id,
        asset=asset,
        user_id=actor.id,
        access_token_id=access_token.id if access_token else None,
        target_subdirectory=subdirectory.as_posix(),
        transfer_mode=transfer_mode,
        status="active",
        expires_at=expires_at,
    )
    session.add(task)
    session.flush()
    return task


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
    staging_relative_path = (PurePosixPath(UPLOAD_STAGING_DIRECTORY) / str(upload_id)).as_posix()
    staging_destination = root / staging_relative_path
    archive_relative_path = (PurePosixPath(asset.type.value) / asset.slug / subdirectory).as_posix()
    remote_login = f"{ssh_user.strip()}@{ssh_host.strip()}"
    remote_mkdir = f"mkdir -p -- {quote(staging_destination.as_posix())}"
    recursive = "-r " if payload.recursive else ""
    completion_marker = staging_destination / UPLOAD_COMPLETION_MARKER
    mark_complete = f"touch -- {quote(completion_marker.as_posix())}"
    command = (
        f"ssh -p {ssh_port} {quote(remote_login)} {quote(remote_mkdir)} && "
        f"scp -P {ssh_port} {recursive}-- {quote(source_path)} "
        f"{quote(f'{remote_login}:{staging_destination.as_posix()}/')} && "
        f"ssh -p {ssh_port} {quote(remote_login)} {quote(mark_complete)}"
    )
    upload_token, expires_at = create_upload_token(
        upload_id, asset.id, subdirectory.as_posix(), ssh_user.strip()
    )
    _new_upload_task(
        session,
        upload_id=upload_id,
        asset=asset,
        actor=actor or asset.owner,
        access_token=None,
        subdirectory=subdirectory,
        expires_at=expires_at,
        transfer_mode="scp",
    )
    if actor:
        record_activity(
            session,
            asset=asset,
            actor=actor,
            action=ActivityAction.PREPARED_UPLOAD,
            description=f"为 {archive_relative_path} 生成了上传指令",
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
    access_token: PersonalAccessToken,
) -> AgentUploadCreateResponse:
    asset = session.get(Asset, asset_id)
    if not asset or asset.archived_at:
        raise UploadCommandError("目标资产不存在或已归档。")
    subdirectory = _validated_subdirectory(asset, target_subdirectory)
    upload_id = uuid4()
    archive_relative_path = (PurePosixPath(asset.type.value) / asset.slug / subdirectory).as_posix()
    upload_token, expires_at = create_upload_token(
        upload_id, asset.id, subdirectory.as_posix(), actor.username or ""
    )
    _new_upload_task(
        session,
        upload_id=upload_id,
        asset=asset,
        actor=actor,
        access_token=access_token,
        subdirectory=subdirectory,
        expires_at=expires_at,
        transfer_mode="agent",
    )
    record_activity(
        session,
        asset=asset,
        actor=actor,
        credential_name=access_token.name,
        action=ActivityAction.PREPARED_UPLOAD,
        description=f"为 {archive_relative_path} 创建了 AI 上传任务",
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
        status_url=f"/api/agent/uploads/{upload_id}",
        finalize_url=f"/api/agent/uploads/{upload_id}/finalize",
        cancel_url=f"/api/agent/uploads/{upload_id}",
    )


def _agent_upload_task(
    session: Session,
    upload_id: UUID,
    upload_token: str,
    actor: User,
    access_token: PersonalAccessToken,
    *,
    for_update: bool = False,
) -> UploadTask:
    claims = read_upload_token(upload_token)
    statement = select(UploadTask).where(UploadTask.id == upload_id)
    if for_update:
        statement = statement.with_for_update()
    task = session.scalar(statement)
    if (
        not claims
        or claims.upload_id != upload_id
        or claims.username != (actor.username or "")
        or not task
        or task.user_id != actor.id
        or task.access_token_id != access_token.id
        or task.asset_id != claims.asset_id
        or task.target_subdirectory != claims.target_subdirectory
        or task.transfer_mode != "agent"
    ):
        raise UploadTicketError("上传凭据无效或已过期，请重新创建上传任务。")
    return task


def validate_agent_upload(
    session: Session,
    upload_id: UUID,
    upload_token: str,
    actor: User,
    access_token: PersonalAccessToken,
) -> UploadTask:
    task = _agent_upload_task(
        session,
        upload_id,
        upload_token,
        actor,
        access_token,
    )
    if task.status != "active":
        raise UploadTicketError("上传任务已结束，请重新创建上传任务。")
    return task


def staged_upload_destination(
    storage_root: Path,
    upload_id: UUID,
    relative_path: str,
) -> tuple[Path, Path]:
    path_parts = relative_path.split("/")
    if (
        len(relative_path) > MAX_ARCHIVE_RELATIVE_PATH_LENGTH
        or "\x00" in relative_path
        or relative_path.startswith("/")
        or any(part in {"", ".", ".."} for part in path_parts)
        or any(len(part) > 255 for part in path_parts)
    ):
        raise UploadContentError("上传文件路径必须是任务内的安全相对路径。")
    upload_path = PurePosixPath(*path_parts)
    try:
        root = storage_root.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise UploadContentError("存储根不可用，无法接收文件。") from error
    if not root.is_dir():
        raise UploadContentError("存储根不可用，无法接收文件。")
    staging_root = root / UPLOAD_STAGING_DIRECTORY
    if staging_root.is_symlink():
        raise UploadContentError("上传临时区不是有效目录，无法接收文件。")
    destination = staging_root.joinpath(str(upload_id), *upload_path.parts)
    reserved_names = {
        UPLOAD_COMPLETION_MARKER,
        UPLOAD_LOCKS_DIRECTORY,
        UPLOAD_PARTS_DIRECTORY,
        UPLOAD_STAGING_DIRECTORY,
    }
    if any(part in reserved_names for part in upload_path.parts):
        raise UploadContentError("上传文件路径使用了系统保留名称。")
    if destination.exists() or destination.is_symlink():
        raise UploadConflictError([upload_path.as_posix()])
    if _unsafe_destination_parent(destination, root):
        raise UploadContentError("上传文件的父目录不可用。")
    return destination, staging_root / UPLOAD_PARTS_DIRECTORY


@contextmanager
def temporary_upload_path(parts_directory: Path) -> Iterator[Path]:
    parts_directory.mkdir(parents=True, exist_ok=True)
    temporary_file = parts_directory / str(uuid4())
    try:
        yield temporary_file
    finally:
        temporary_file.unlink(missing_ok=True)
        for directory in (parts_directory, parts_directory.parent):
            try:
                directory.rmdir()
            except OSError:
                break


def complete_agent_file_upload(
    upload_id: UUID,
    relative_path: str,
    temporary_file: Path,
    destination: Path,
    checksum_sha256: str,
) -> AgentUploadedFileResponse:
    staging_root = next(
        (
            ancestor
            for ancestor in destination.parents
            if ancestor.name == UPLOAD_STAGING_DIRECTORY
        ),
        None,
    )
    if staging_root is None:
        raise UploadContentError("上传文件目标不在临时区内。")
    try:
        canonical_relative_path = destination.relative_to(
            staging_root / str(upload_id)
        ).as_posix()
    except ValueError as error:
        raise UploadContentError("上传文件目标不属于当前任务。") from error
    storage_root = staging_root.parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise UploadConflictError([relative_path])
    linked = False
    try:
        os.link(temporary_file, destination)
        linked = True
        _fsync_directory_chain(storage_root, destination.parent)
    except FileExistsError:
        raise UploadConflictError([relative_path]) from None
    except Exception:
        if linked:
            destination.unlink(missing_ok=True)
            with suppress(OSError):
                _fsync_directory_chain(storage_root, destination.parent)
        raise
    temporary_file.unlink()
    return AgentUploadedFileResponse(
        upload_id=upload_id,
        relative_path=canonical_relative_path,
        file_size=destination.stat().st_size,
        checksum_sha256=checksum_sha256,
    )


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory_chain(root: Path, leaf: Path) -> None:
    current = root
    _fsync_directory(current)
    for part in leaf.relative_to(root).parts:
        current /= part
        _fsync_directory(current)


def _staged_files(staging_directory: Path, *, completion_marker_required: bool) -> list[Path]:
    if not staging_directory.exists():
        raise UploadNotReadyError("尚未检测到文件，请确认终端传输已完成后重试。")
    if staging_directory.is_symlink() or not staging_directory.is_dir():
        raise UploadContentError("上传临时区不是有效目录，无法入库。")
    completion_marker = staging_directory / UPLOAD_COMPLETION_MARKER
    if completion_marker_required and not completion_marker.is_file():
        raise UploadNotReadyError("传输尚未完成，请等待上传命令执行结束后重试。")
    if completion_marker.is_symlink():
        raise UploadContentError("上传完成标记无效，无法入库。")

    files: list[Path] = []
    for candidate in staging_directory.rglob("*"):
        if candidate == completion_marker:
            continue
        if candidate.is_symlink():
            raise UploadContentError("上传内容含有符号链接，无法入库。")
        if candidate.is_file():
            files.append(candidate)
        elif not candidate.is_dir():
            raise UploadContentError("上传内容含有不支持的文件类型，无法入库。")
    if not files:
        raise UploadNotReadyError("尚未检测到文件，请确认终端传输已完成后重试。")
    return sorted(files, key=lambda path: path.relative_to(staging_directory).as_posix())


def upload_status(
    session: Session,
    storage_root: Path,
    upload_id: UUID,
    upload_token: str,
    *,
    actor: User,
) -> UploadStatusResponse:
    claims = read_upload_token(upload_token)
    task = session.get(UploadTask, upload_id)
    if (
        not claims
        or claims.upload_id != upload_id
        or claims.username != (actor.username or "")
        or not task
        or task.user_id != actor.id
        or task.access_token_id is not None
        or task.asset_id != claims.asset_id
        or task.target_subdirectory != claims.target_subdirectory
        or task.transfer_mode != "scp"
    ):
        raise UploadTicketError("上传凭据无效或已过期，请重新生成上传命令。")
    if task.status == "completed" and task.result:
        result = UploadFinalizeResponse.model_validate(task.result)
        return UploadStatusResponse(
            upload_id=task.id,
            status="completed",
            uploaded_file_count=result.imported_file_count,
            total_size=result.total_size,
            expires_at=task.expires_at,
        )
    try:
        root = storage_root.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise UploadContentError("存储根不可用，无法检测上传进度。") from error
    if not root.is_dir():
        raise UploadContentError("存储根不可用，无法检测上传进度。")
    staging_directory = root / UPLOAD_STAGING_DIRECTORY / str(upload_id)
    if not staging_directory.exists():
        return UploadStatusResponse(
            upload_id=task.id,
            status="waiting",
            uploaded_file_count=0,
            total_size=0,
            expires_at=task.expires_at,
        )
    try:
        files = _staged_files(staging_directory, completion_marker_required=False)
    except UploadNotReadyError:
        files = []
    try:
        total_size = sum(path.stat().st_size for path in files)
    except OSError as error:
        raise UploadContentError("上传文件正在变化，请等待传输完成。") from error
    completion_marker = staging_directory / UPLOAD_COMPLETION_MARKER
    ready = bool(files) and completion_marker.is_file() and not completion_marker.is_symlink()
    return UploadStatusResponse(
        upload_id=task.id,
        status="ready" if ready else "waiting",
        uploaded_file_count=len(files),
        total_size=total_size,
        expires_at=task.expires_at,
    )


def agent_upload_status(
    session: Session,
    storage_root: Path,
    upload_id: UUID,
    upload_token: str,
    *,
    actor: User,
    access_token: PersonalAccessToken,
) -> AgentUploadStatusResponse:
    task = _agent_upload_task(session, upload_id, upload_token, actor, access_token)
    if task.status == "completed" and task.result:
        result = UploadFinalizeResponse.model_validate(task.result)
        return AgentUploadStatusResponse(
            upload_id=task.id,
            status="completed",
            uploaded_file_count=result.imported_file_count,
            total_size=result.total_size,
            expires_at=task.expires_at,
        )
    if task.status == "cancelled":
        return AgentUploadStatusResponse(
            upload_id=task.id,
            status="cancelled",
            uploaded_file_count=0,
            total_size=0,
            expires_at=task.expires_at,
        )

    try:
        root = storage_root.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise UploadContentError("存储根不可用，无法检测上传进度。") from error
    if not root.is_dir():
        raise UploadContentError("存储根不可用，无法检测上传进度。")
    staging_root = root / UPLOAD_STAGING_DIRECTORY
    if staging_root.is_symlink():
        raise UploadContentError("上传临时区不是有效目录，无法检测上传进度。")
    staging_directory = staging_root / str(upload_id)
    if not staging_directory.exists():
        return AgentUploadStatusResponse(
            upload_id=task.id,
            status="waiting",
            uploaded_file_count=0,
            total_size=0,
            expires_at=task.expires_at,
        )
    try:
        files = _staged_files(staging_directory, completion_marker_required=False)
    except UploadNotReadyError:
        files = []
    uploaded_files: list[AgentUploadFileStatus] = []
    try:
        for path in files:
            uploaded_files.append(
                AgentUploadFileStatus(
                    relative_path=path.relative_to(staging_directory).as_posix(),
                    file_size=path.stat().st_size,
                )
            )
    except OSError as error:
        raise UploadContentError("上传文件正在变化，请稍后重试。") from error
    return AgentUploadStatusResponse(
        upload_id=task.id,
        status="ready" if uploaded_files else "waiting",
        uploaded_file_count=len(uploaded_files),
        total_size=sum(file.file_size for file in uploaded_files),
        expires_at=task.expires_at,
        files=uploaded_files,
    )


def cancel_agent_upload(
    session: Session,
    storage_root: Path,
    upload_id: UUID,
    upload_token: str,
    *,
    actor: User,
    access_token: PersonalAccessToken,
) -> AgentUploadCancelResponse:
    with upload_task_guard(storage_root, upload_id):
        return _cancel_agent_upload(
            session,
            storage_root,
            upload_id,
            upload_token,
            actor=actor,
            access_token=access_token,
        )


def _cancel_agent_upload(
    session: Session,
    storage_root: Path,
    upload_id: UUID,
    upload_token: str,
    *,
    actor: User,
    access_token: PersonalAccessToken,
) -> AgentUploadCancelResponse:
    task = _agent_upload_task(
        session,
        upload_id,
        upload_token,
        actor,
        access_token,
        for_update=True,
    )
    if task.status == "completed":
        raise UploadContentError("已完成的上传任务不能取消。")

    try:
        root = storage_root.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise UploadContentError("存储根不可用，无法取消上传任务。") from error
    if not root.is_dir():
        raise UploadContentError("存储根不可用，无法取消上传任务。")
    staging_root = root / UPLOAD_STAGING_DIRECTORY
    if staging_root.is_symlink():
        raise UploadContentError("上传临时区不是有效目录，无法取消上传任务。")
    staging_directory = staging_root / str(upload_id)
    if staging_directory.is_symlink() or (
        staging_directory.exists() and not staging_directory.is_dir()
    ):
        raise UploadContentError("上传任务临时目录无效，无法安全清理。")

    if task.status != "cancelled":
        task.status = "cancelled"
        task.completed_at = datetime.now(UTC)
        session.flush()
        session.commit()

    if staging_directory.exists():
        try:
            shutil.rmtree(staging_directory)
        except OSError as error:
            raise UploadContentError("上传任务已取消，但临时文件清理失败，请重试。") from error
    if staging_root.exists():
        with suppress(OSError):
            if not any(staging_root.iterdir()):
                staging_root.rmdir()
    return AgentUploadCancelResponse(upload_id=task.id, status="cancelled")


def _open_staged_files(staging_directory: Path, staged_files: list[Path]) -> list[OpenStagedFile]:
    opened: list[OpenStagedFile] = []
    try:
        for path in staged_files:
            relative_path = PurePosixPath(path.relative_to(staging_directory).as_posix())
            parent_descriptor = os.open(
                staging_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            descriptor: int | None = None
            try:
                for directory_name in relative_path.parts[:-1]:
                    next_descriptor = os.open(
                        directory_name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent_descriptor,
                    )
                    os.close(parent_descriptor)
                    parent_descriptor = next_descriptor
                descriptor = os.open(
                    relative_path.name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=parent_descriptor,
                )
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise UploadContentError("上传内容含有不支持的文件类型，无法入库。")
                opened.append(
                    OpenStagedFile(
                        relative_path=relative_path,
                        descriptor=descriptor,
                        parent_descriptor=parent_descriptor,
                        name=relative_path.name,
                        metadata=metadata,
                    )
                )
                descriptor = None
                parent_descriptor = -1
            except UploadContentError:
                raise
            except OSError as error:
                raise UploadContentError(
                    "上传文件路径在入库期间发生变化，请重新上传后重试。"
                ) from error
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if parent_descriptor >= 0:
                    os.close(parent_descriptor)
    except Exception:
        _close_staged_files(opened)
        raise
    return opened


def _close_staged_files(files: list[OpenStagedFile]) -> None:
    for source in files:
        os.close(source.descriptor)
        os.close(source.parent_descriptor)


def _unsafe_destination_parent(destination: Path, storage_root: Path) -> bool:
    parent = storage_root
    for part in destination.relative_to(storage_root).parts[:-1]:
        parent /= part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            return True
    return False


def _remove_empty_archive_directories(directories: set[Path], storage_root: Path) -> None:
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        if directory == storage_root or not directory.exists():
            continue
        try:
            directory.rmdir()
        except OSError:
            continue


def _remove_upload_staging(
    storage_root: Path,
    upload_id: UUID,
    *,
    cleanup_error: str,
) -> None:
    try:
        root = storage_root.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise UploadContentError(cleanup_error) from error
    if not root.is_dir():
        raise UploadContentError(cleanup_error)
    staging_root = root / UPLOAD_STAGING_DIRECTORY
    if not staging_root.exists():
        return
    if staging_root.is_symlink() or not staging_root.is_dir():
        raise UploadContentError(cleanup_error)
    staging_directory = staging_root / str(upload_id)
    if staging_directory.is_symlink() or (
        staging_directory.exists() and not staging_directory.is_dir()
    ):
        raise UploadContentError(cleanup_error)
    try:
        if staging_directory.exists():
            shutil.rmtree(staging_directory)
            _fsync_directory(staging_root)
        if not any(staging_root.iterdir()):
            staging_root.rmdir()
            _fsync_directory(root)
    except OSError as error:
        raise UploadContentError(cleanup_error) from error


def _cleanup_completed_upload_staging(storage_root: Path, upload_id: UUID) -> None:
    _remove_upload_staging(
        storage_root,
        upload_id,
        cleanup_error="文件已完成入库，但临时区清理失败，请重试完成请求。",
    )


def cleanup_expired_upload_tasks(
    session: Session,
    storage_root: Path,
    *,
    limit: int = 100,
) -> int:
    now = datetime.now(UTC)
    candidate_ids = session.scalars(
        select(UploadTask.id)
        .where(UploadTask.expires_at <= now)
        .order_by(UploadTask.expires_at, UploadTask.id)
        .limit(max(1, min(limit, 100)))
    ).all()
    cleaned = 0
    for upload_id in candidate_ids:
        try:
            with upload_task_guard(storage_root, upload_id):
                task = session.get(UploadTask, upload_id, with_for_update=True)
                if not task:
                    session.rollback()
                    continue
                expires_at = task.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at > now:
                    session.rollback()
                    continue
                _remove_upload_staging(
                    storage_root,
                    upload_id,
                    cleanup_error="过期上传任务临时区清理失败。",
                )
                session.delete(task)
                session.commit()
                cleaned += 1
        except UploadContentError:
            session.rollback()
    return cleaned


def _copy_without_overwrite(
    source: OpenStagedFile,
    destination_parent_descriptor: int,
    destination: Path,
    relative_path: str,
) -> str:
    target_descriptor: int | None = None
    checksum_sha256 = ""
    try:
        target_descriptor = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            source.metadata.st_mode & 0o777,
            dir_fd=destination_parent_descriptor,
        )
    except FileExistsError:
        raise UploadConflictError([relative_path]) from None
    try:
        with (
            os.fdopen(os.dup(source.descriptor), "rb") as input_file,
            os.fdopen(target_descriptor, "wb") as output_file,
        ):
            target_descriptor = None
            digest = hashlib.sha256()
            while chunk := input_file.read(1024 * 1024):
                digest.update(chunk)
                output_file.write(chunk)
            output_file.flush()
            os.fsync(output_file.fileno())
            checksum_sha256 = digest.hexdigest()
        try:
            current_metadata = os.fstat(source.descriptor)
            path_metadata = os.stat(
                source.name,
                dir_fd=source.parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise UploadContentError("上传文件在入库期间发生变化，请重新上传后重试。") from error
        identity = (source.metadata.st_dev, source.metadata.st_ino)
        current_identity = (current_metadata.st_dev, current_metadata.st_ino)
        path_identity = (path_metadata.st_dev, path_metadata.st_ino)
        stable_fields = (
            source.metadata.st_size,
            source.metadata.st_mtime_ns,
            source.metadata.st_ctime_ns,
        )
        current_fields = (
            current_metadata.st_size,
            current_metadata.st_mtime_ns,
            current_metadata.st_ctime_ns,
        )
        if (
            identity != current_identity
            or identity != path_identity
            or stable_fields != current_fields
        ):
            raise UploadContentError("上传文件在入库期间发生变化，请重新上传后重试。")
        os.fsync(destination_parent_descriptor)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(destination.name, dir_fd=destination_parent_descriptor)
        with suppress(OSError):
            os.fsync(destination_parent_descriptor)
        raise
    finally:
        if target_descriptor is not None:
            os.close(target_descriptor)
    return checksum_sha256


def finalize_upload(
    session: Session,
    storage_root: Path,
    upload_id: UUID,
    upload_token: str,
    *,
    actor: User,
    access_token: PersonalAccessToken | None = None,
) -> UploadFinalizeResponse:
    with upload_task_guard(storage_root, upload_id):
        try:
            with storage_index_guard(session, shared=True):
                return _finalize_upload(
                    session,
                    storage_root,
                    upload_id,
                    upload_token,
                    actor=actor,
                    access_token=access_token,
                )
        except StorageIndexBusyError:
            raise UploadBusyError(
                "归档扫描正在运行，请等待扫描完成后重试入库。"
            ) from None


def _finalize_upload(
    session: Session,
    storage_root: Path,
    upload_id: UUID,
    upload_token: str,
    *,
    actor: User,
    access_token: PersonalAccessToken | None = None,
) -> UploadFinalizeResponse:
    claims = read_upload_token(upload_token)
    task = session.scalar(select(UploadTask).where(UploadTask.id == upload_id).with_for_update())
    if (
        not claims
        or claims.upload_id != upload_id
        or claims.username != (actor.username or "")
        or not task
        or task.user_id != actor.id
        or task.access_token_id != (access_token.id if access_token else None)
        or task.asset_id != claims.asset_id
        or task.target_subdirectory != claims.target_subdirectory
    ):
        raise UploadTicketError("上传凭据无效或已过期，请重新生成上传命令。")
    if task.status == "completed" and task.result:
        result = UploadFinalizeResponse.model_validate(task.result)
        _cleanup_completed_upload_staging(storage_root, upload_id)
        return result
    if task.status != "active":
        raise UploadTicketError("上传任务已取消，请重新创建上传任务。")

    asset = session.scalar(select(Asset).where(Asset.id == claims.asset_id).with_for_update())
    if not asset or asset.archived_at:
        raise UploadTicketError("目标资产不存在或已归档，请重新生成上传命令。")
    subdirectory = _validated_subdirectory(asset, task.target_subdirectory)
    try:
        root = storage_root.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise UploadContentError("存储根不可用，无法完成入库。") from error
    if not root.is_dir():
        raise UploadContentError("存储根不可用，无法完成入库。")

    staging_root = root / UPLOAD_STAGING_DIRECTORY
    staging_directory = staging_root / str(upload_id)
    if staging_root.is_symlink():
        raise UploadContentError("上传临时区不是有效目录，无法入库。")
    staged_files = _staged_files(
        staging_directory,
        completion_marker_required=task.transfer_mode == "scp",
    )
    opened_files = _open_staged_files(staging_directory, staged_files)
    archive_directory = root.joinpath(asset.type.value, asset.slug, *subdirectory.parts)
    destinations = [
        archive_directory.joinpath(*source.relative_path.parts) for source in opened_files
    ]
    relative_paths = [destination.relative_to(root).as_posix() for destination in destinations]

    try:
        if any(len(path) > MAX_ARCHIVE_RELATIVE_PATH_LENGTH for path in relative_paths):
            raise UploadContentError(
                "归档路径超过数据库允许的 1000 个字符，请缩短目录或文件名。"
            )
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
    except Exception:
        _close_staged_files(opened_files)
        raise

    published_files: list[PublishedFile] = []
    created_archive_directories: set[Path] = set()
    total_size = 0
    try:
        for source, destination in zip(opened_files, destinations, strict=True):
            relative_path = destination.relative_to(root).as_posix()
            parent_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                parent_path = root
                for directory_name in destination.relative_to(root).parts[:-1]:
                    parent_path /= directory_name
                    try:
                        os.mkdir(directory_name, dir_fd=parent_descriptor)
                        created_archive_directories.add(parent_path)
                        os.fsync(parent_descriptor)
                    except FileExistsError:
                        pass
                    next_descriptor = os.open(
                        directory_name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent_descriptor,
                    )
                    os.close(parent_descriptor)
                    parent_descriptor = next_descriptor
                rollback_descriptor = os.dup(parent_descriptor)
                try:
                    checksum_sha256 = _copy_without_overwrite(
                        source,
                        parent_descriptor,
                        destination,
                        relative_path,
                    )
                except Exception:
                    os.close(rollback_descriptor)
                    raise
                published_files.append(
                    PublishedFile(
                        path=destination,
                        parent_descriptor=rollback_descriptor,
                        checksum_sha256=checksum_sha256,
                    )
                )
            finally:
                os.close(parent_descriptor)

        checksum_paths: dict[str, str] = {}
        duplicate_paths: list[str] = []
        for published, relative_path in zip(published_files, relative_paths, strict=True):
            original_path = checksum_paths.setdefault(published.checksum_sha256, relative_path)
            if original_path != relative_path:
                duplicate_paths.append(f"{relative_path} 与 {original_path}")
        existing_checksums = {
            checksum: relative_path
            for checksum, relative_path in session.execute(
                select(FileRecord.checksum, FileRecord.relative_path).where(
                    FileRecord.asset_id == asset.id,
                    FileRecord.checksum.in_(checksum_paths),
                )
            ).all()
            if checksum
        }
        duplicate_paths.extend(
            f"{relative_path} 与已归档文件 {existing_checksums[checksum]}"
            for checksum, relative_path in checksum_paths.items()
            if checksum in existing_checksums
        )
        if duplicate_paths:
            visible = "\n".join(f"- {path}" for path in duplicate_paths[:8])
            raise UploadContentError(f"检测到内容相同的重复文件：\n{visible}")

        for published, relative_path in zip(published_files, relative_paths, strict=True):
            metadata = os.stat(
                published.path.name,
                dir_fd=published.parent_descriptor,
                follow_symlinks=False,
            )
            total_size += metadata.st_size
            session.add(
                FileRecord(
                    asset=asset,
                    relative_path=relative_path,
                    file_name=published.path.name,
                    file_kind=file_kind(published.path),
                    mime_type=guess_type(published.path.name)[0],
                    file_size=metadata.st_size,
                    checksum=published.checksum_sha256,
                    health_status=HealthStatus.HEALTHY,
                    modified_at=datetime.fromtimestamp(metadata.st_mtime, UTC),
                )
            )
        record_activity(
            session,
            asset=asset,
            actor=actor,
            credential_name=access_token.name if access_token else None,
            action=ActivityAction.COMPLETED_UPLOAD,
            description=(
                f"向 {asset.type.value}/{asset.slug}/{subdirectory.as_posix()} "
                f"入库了 {len(destinations)} 个文件"
            ),
        )
        result = UploadFinalizeResponse(
            asset_id=asset.id,
            imported_file_count=len(destinations),
            total_size=total_size,
            relative_paths=relative_paths,
            checksums={
                relative_path: published.checksum_sha256
                for published, relative_path in zip(published_files, relative_paths, strict=True)
            },
        )
        task.status = "completed"
        task.result = result.model_dump(mode="json")
        task.completed_at = datetime.now(UTC)
        session.flush()
        session.commit()
    except Exception:
        try:
            for published in reversed(published_files):
                with suppress(FileNotFoundError):
                    os.unlink(published.path.name, dir_fd=published.parent_descriptor)
                with suppress(OSError):
                    os.fsync(published.parent_descriptor)
            _remove_empty_archive_directories(created_archive_directories, root)
        finally:
            # PostgreSQL transaction locks must cover filesystem compensation too.
            session.rollback()
        raise
    finally:
        _close_staged_files(opened_files)
        for published in published_files:
            os.close(published.parent_descriptor)

    _cleanup_completed_upload_staging(storage_root, upload_id)
    return result
