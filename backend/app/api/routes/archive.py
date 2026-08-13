from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import AdminDependency, require_admin
from app.core.config import settings
from app.db.session import get_session
from app.domain.schemas import (
    ArchiveHealthSummary,
    ClaimUnclaimedFileRequest,
    FileClaimResult,
    ScanRunSummary,
    UnclaimedFileSummary,
    UploadCommandRequest,
    UploadCommandResponse,
)
from app.services.archive import StorageScanError, archive_health, scan_storage
from app.services.transfers import UploadCommandError, generate_upload_command
from app.services.unclaimed import (
    AssetNotFoundError,
    ClaimSourceFileError,
    FileAlreadyClaimedError,
    FilePathConflictError,
    UnclaimedFileNotFoundError,
    claim_unclaimed_file,
    list_unclaimed_files,
)

router = APIRouter(
    prefix="/archive",
    tags=["archive"],
    dependencies=[Depends(require_admin)],
)
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health(session: SessionDependency) -> ArchiveHealthSummary:
    return archive_health(session, settings.storage_root)


@router.get("/unclaimed")
def unclaimed_files(session: SessionDependency) -> list[UnclaimedFileSummary]:
    return list_unclaimed_files(session)


@router.post("/unclaimed/{unclaimed_file_id}/claim")
def claim_file(
    unclaimed_file_id: UUID,
    payload: ClaimUnclaimedFileRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> FileClaimResult:
    try:
        result = claim_unclaimed_file(
            session, settings.storage_root, unclaimed_file_id, payload.asset_id, actor=current_user
        )
        session.commit()
        return result
    except UnclaimedFileNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="待认领文件不存在。") from None
    except AssetNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="目标资产不存在或已归档。") from None
    except FileAlreadyClaimedError:
        session.rollback()
        raise HTTPException(status_code=409, detail="该文件已被认领。") from None
    except FilePathConflictError:
        session.rollback()
        raise HTTPException(status_code=409, detail="该归档路径已归属于其他资产。") from None
    except ClaimSourceFileError:
        session.rollback()
        raise HTTPException(status_code=409, detail="源文件不可用，无法认领。") from None
    except Exception:
        session.rollback()
        raise


@router.post("/upload-command")
def upload_command(
    payload: UploadCommandRequest, session: SessionDependency, current_user: AdminDependency
) -> UploadCommandResponse:
    try:
        result = generate_upload_command(
            session,
            payload,
            ssh_host=settings.upload_ssh_host,
            ssh_user=current_user.username or "",
            ssh_port=settings.upload_ssh_port,
            destination_root=settings.upload_destination_root,
            actor=current_user,
        )
        session.commit()
        return result
    except UploadCommandError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise


@router.post("/scans", status_code=status.HTTP_201_CREATED)
def create_scan(session: SessionDependency, current_user: AdminDependency) -> ScanRunSummary:
    try:
        result = scan_storage(session, settings.storage_root)
        session.commit()
        return result
    except StorageScanError:
        session.commit()
        raise HTTPException(status_code=409, detail="存储根不可用，无法执行扫描。") from None
    except Exception:
        session.rollback()
        raise
