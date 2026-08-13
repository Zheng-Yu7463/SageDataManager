from __future__ import annotations

from pathlib import PurePosixPath
from shlex import quote

from sqlalchemy.orm import Session

from app.domain.activity import ActivityAction
from app.domain.models import Activity, Asset, User
from app.domain.schemas import UploadCommandRequest, UploadCommandResponse
from app.services.upload_directories import upload_directory_names


class UploadCommandError(Exception):
    pass


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

    subdirectory = PurePosixPath(payload.target_subdirectory.strip())
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

    root = PurePosixPath(destination_root.strip())
    if not root.is_absolute():
        raise UploadCommandError("SCP 目标根目录必须使用绝对路径。")
    destination = root / asset.type.value / asset.slug / subdirectory
    archive_relative_path = (PurePosixPath(asset.type.value) / asset.slug / subdirectory).as_posix()
    remote_login = f"{ssh_user.strip()}@{ssh_host.strip()}"
    source_path = payload.source_path.strip()
    remote_mkdir = f"mkdir -p -- {quote(destination.as_posix())}"
    recursive = "-r " if payload.recursive else ""
    command = (
        f"ssh -p {ssh_port} {quote(remote_login)} {quote(remote_mkdir)} && "
        f"scp -P {ssh_port} {recursive}-- {quote(source_path)} "
        f"{quote(f'{remote_login}:{destination.as_posix()}/')}"
    )
    if actor:
        session.add(
            Activity(
                asset=asset,
                actor=actor,
                action=ActivityAction.PREPARED_UPLOAD,
                description=f"为 {archive_relative_path} 生成了 SCP 上传指令",
            )
        )
    return UploadCommandResponse(
        asset_id=asset.id,
        asset_title=asset.title,
        archive_relative_path=archive_relative_path,
        command=command,
    )
