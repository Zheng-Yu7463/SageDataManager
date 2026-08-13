import os
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.types import Send

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
    open_file_delivery,
)
from app.services.security import create_file_access_token, read_file_access_token

router = APIRouter(prefix="/files", tags=["files"])
SessionDependency = Annotated[Session, Depends(get_session)]


class OpenFileResponse(FileResponse):
    def __init__(
        self,
        descriptor: int,
        *,
        file_name: str,
        stat_result: os.stat_result,
        media_type: str,
        content_disposition_type: str,
        headers: dict[str, str],
    ) -> None:
        self.descriptor = descriptor
        try:
            super().__init__(
                file_name,
                filename=file_name,
                stat_result=stat_result,
                media_type=media_type,
                content_disposition_type=content_disposition_type,
                headers=headers,
            )
        except Exception:
            os.close(self.descriptor)
            raise

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            os.close(self.descriptor)

    async def _read(self, size: int, offset: int) -> bytes:
        return await anyio.to_thread.run_sync(os.pread, self.descriptor, size, offset)

    async def _send_bytes(self, send: Send, start: int, end: int) -> None:
        if start == end:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        while start < end:
            chunk = await self._read(min(self.chunk_size, end - start), start)
            if not chunk:
                raise RuntimeError("Archive file changed while it was being delivered.")
            start += len(chunk)
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": start < end,
                }
            )

    async def _handle_simple(
        self, send: Send, send_header_only: bool, send_pathsend: bool
    ) -> None:
        await send(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await self._send_bytes(send, 0, self.stat_result.st_size)

    async def _handle_single_range(
        self, send: Send, start: int, end: int, file_size: int, send_header_only: bool
    ) -> None:
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        headers["content-length"] = str(end - start)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await self._send_bytes(send, start, end)

    async def _handle_multiple_ranges(
        self,
        send: Send,
        ranges: list[tuple[int, int]],
        file_size: int,
        send_header_only: bool,
    ) -> None:
        boundary = os.urandom(13).hex()
        content_length, header_generator = self.generate_multipart(
            ranges, boundary, file_size, self.headers["content-type"]
        )
        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-type"] = f"multipart/byteranges; boundary={boundary}"
        headers["content-length"] = str(content_length)
        await send({"type": "http.response.start", "status": 206, "headers": headers.raw})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        for start, end in ranges:
            await send(
                {
                    "type": "http.response.body",
                    "body": header_generator(start, end),
                    "more_body": True,
                }
            )
            offset = start
            while offset < end:
                chunk = await self._read(min(self.chunk_size, end - offset), offset)
                if not chunk:
                    raise RuntimeError("Archive file changed while it was being delivered.")
                offset += len(chunk)
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
            await send({"type": "http.response.body", "body": b"\r\n", "more_body": True})
        await send(
            {
                "type": "http.response.body",
                "body": f"--{boundary}--".encode("latin-1"),
                "more_body": False,
            }
        )


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
    delivery = None
    claims = read_file_access_token(ticket)
    if not claims:
        raise HTTPException(status_code=403, detail="文件访问链接无效或已过期。")
    try:
        actor, mode, audit_access = authorize_file_access_grant(
            session, claims.grant_id, file_id
        )
        delivery = open_file_delivery(
            session,
            settings.storage_root,
            file_id,
            mode,
            actor=actor,
            audit_access=audit_access,
        )
        session.commit()
    except FileAccessGrantInvalidError:
        if delivery:
            delivery.close()
        session.rollback()
        raise HTTPException(status_code=403, detail="文件访问链接无效或已过期。") from None
    except (FileNotFoundError, FilePreviewUnavailableError, FileUnavailableError) as error:
        if delivery:
            delivery.close()
        session.rollback()
        _raise_access_error(error)
    except Exception:
        if delivery:
            delivery.close()
        session.rollback()
        raise

    return OpenFileResponse(
        delivery.take_descriptor(),
        file_name=delivery.file_name,
        stat_result=delivery.stat,
        media_type=delivery.media_type,
        content_disposition_type=delivery.content_disposition,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
