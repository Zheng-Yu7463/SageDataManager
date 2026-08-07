from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import AdminDependency
from app.core.config import settings
from app.db.session import get_session
from app.domain.schemas import FileAccessTicketRequest, FileAccessTicketResponse
from app.services.accounts import get_active_account
from app.services.file_access import (
    FileNotFoundError,
    FilePreviewUnavailableError,
    FileUnavailableError,
    prepare_file_delivery,
    verify_file_access,
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
        verify_file_access(session, file_id, payload.mode)
    except (FileNotFoundError, FilePreviewUnavailableError, FileUnavailableError) as error:
        _raise_access_error(error)

    username = current_user.username
    if not username:
        raise HTTPException(status_code=403, detail="当前账号没有可用的服务器用户名。")
    ticket, expires_at = create_file_access_token(file_id, payload.mode, username)
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
    if not claims or claims.file_id != file_id:
        raise HTTPException(status_code=403, detail="文件访问链接无效或已过期。")
    actor = get_active_account(session, claims.username)
    if not actor or actor.role != "admin":
        raise HTTPException(status_code=403, detail="文件访问账号不可用。")
    try:
        delivery = prepare_file_delivery(
            session, settings.storage_root, file_id, claims.mode, actor=actor
        )
        session.commit()
    except (FileNotFoundError, FilePreviewUnavailableError, FileUnavailableError) as error:
        session.rollback()
        _raise_access_error(error)
    except Exception:
        session.rollback()
        raise

    return Response(
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": delivery.content_disposition,
            "Content-Type": delivery.media_type,
            "X-Accel-Redirect": delivery.internal_uri,
            "X-Content-Type-Options": "nosniff",
        }
    )
