from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.dependencies import AdminDependency
from app.core.config import settings
from app.db.session import get_session
from app.domain.schemas import FileAccessTicketRequest, FileAccessTicketResponse
from app.services.file_access import (
    FileAccessGrantInvalidError,
    FileNotFoundError,
    FilePreviewUnavailableError,
    FileUnavailableError,
    authorize_file_access_grant,
    issue_file_access_grant,
    prepare_file_delivery,
)
from app.services.security import create_file_access_token, read_file_access_token

router = APIRouter(prefix="/files", tags=["files"])
SessionDependency = Annotated[Session, Depends(get_session)]


def _raise_access_error(error: Exception) -> None:
    if isinstance(error, FileNotFoundError):
        raise HTTPException(status_code=404, detail="文件不存在或所属资产已归档。") from None
    if isinstance(error, FilePreviewUnavailableError):
        raise HTTPException(
            status_code=409, detail="此文件类型暂不支持预览，请下载后查看。"
        ) from None
    raise HTTPException(status_code=409, detail="文件当前不可用，请先重新扫描归档。") from None


@router.post("/{file_id}/tickets", status_code=status.HTTP_201_CREATED)
def create_access_ticket(
    file_id: UUID,
    payload: FileAccessTicketRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> FileAccessTicketResponse:
    try:
        grant = issue_file_access_grant(
            session,
            file_id,
            payload.mode,
            actor=current_user,
            ttl_seconds=settings.file_access_ttl_seconds,
        )
        ticket = create_file_access_token(grant.id, grant.expires_at)
        expires_at = grant.expires_at
        session.commit()
    except (FileNotFoundError, FilePreviewUnavailableError, FileUnavailableError) as error:
        session.rollback()
        _raise_access_error(error)
    except Exception:
        session.rollback()
        raise

    return FileAccessTicketResponse(
        content_url=(
            f"{settings.api_prefix}/files/{file_id}/content?ticket={quote(ticket, safe='')}"
        ),
        expires_at=expires_at,
    )


@router.get("/{file_id}/content")
def content(
    file_id: UUID,
    ticket: Annotated[str, Query(min_length=1, max_length=2000)],
    session: SessionDependency,
) -> Response:
    claims = read_file_access_token(ticket)
    if not claims:
        raise HTTPException(status_code=403, detail="文件访问链接无效或已过期。")
    try:
        actor, mode, audit_access = authorize_file_access_grant(
            session, claims.grant_id, file_id
        )
        delivery = prepare_file_delivery(
            session,
            settings.storage_root,
            file_id,
            mode,
            actor=actor,
            audit_access=audit_access,
        )
        session.commit()
    except FileAccessGrantInvalidError:
        session.rollback()
        raise HTTPException(status_code=403, detail="文件访问链接无效或已过期。") from None
    except (FileNotFoundError, FilePreviewUnavailableError, FileUnavailableError) as error:
        session.rollback()
        _raise_access_error(error)
    except Exception:
        session.rollback()
        raise

    return FileResponse(
        path=delivery.path,
        filename=delivery.path.name,
        media_type=delivery.media_type,
        content_disposition_type=delivery.content_disposition,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
