from __future__ import annotations

import hashlib
import hmac
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import AgentPrincipal, require_agent, require_agent_scope
from app.core.config import settings
from app.db.session import get_session
from app.domain.enums import AssetType
from app.domain.schemas import (
    AgentIdentityResponse,
    AgentUploadCreateRequest,
    AgentUploadCreateResponse,
    AgentUploadedFileResponse,
    AssetCreateRequest,
    AssetDetail,
    AssetListResponse,
    AssetSummary,
    AssetUpdateRequest,
    PublicationCitationResponse,
    UploadFinalizeRequest,
    UploadFinalizeResponse,
)
from app.services.access_tokens import record_access_token_use
from app.services.assets import (
    AssetMetadataError,
    AssetNotFoundError,
    AssetSlugConflictError,
    create_asset,
    get_asset,
    list_assets,
    update_asset,
)
from app.services.citations import PublicationCitationError, build_publication_citation
from app.services.transfers import (
    UploadCommandError,
    UploadConflictError,
    UploadContentError,
    UploadNotReadyError,
    UploadTicketError,
    UploadTooLargeError,
    complete_agent_file_upload,
    create_agent_upload,
    finalize_upload,
    staged_upload_destination,
    temporary_upload_path,
    validate_agent_upload,
)

router = APIRouter(prefix="/agent", tags=["agent"])
SessionDependency = Annotated[Session, Depends(get_session)]


def scoped(scope: str):
    return Depends(require_agent_scope(scope))


@router.get("/me")
def agent_identity(
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, Depends(require_agent)],
) -> AgentIdentityResponse:
    record_access_token_use(session, principal.token)
    return AgentIdentityResponse(
        username=principal.user.username or "",
        account_name=principal.user.name,
        credential_name=principal.token.name,
        scopes=principal.token.scopes,
        expires_at=principal.token.expires_at,
    )


@router.get("/assets")
def agent_assets(
    session: SessionDependency,
    _: Annotated[AgentPrincipal, scoped("assets:read")],
    asset_type: AssetType | None = None,
    query: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> AssetListResponse:
    items, total = list_assets(
        session,
        asset_type=asset_type,
        query=query,
        page=page,
        status=None,
        visibility=None,
        has_files=None,
        venue=None,
        year=None,
        page_size=page_size,
    )
    return AssetListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/assets", status_code=status.HTTP_201_CREATED)
def agent_create_asset(
    payload: AssetCreateRequest,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("metadata:write")],
) -> AssetSummary:
    try:
        result = create_asset(
            session,
            payload,
            actor=principal.user,
            credential_name=principal.token.name,
        )
        session.commit()
        return result
    except AssetSlugConflictError:
        session.rollback()
        raise HTTPException(status_code=409, detail="资产标识已存在，请使用另一个 slug。") from None
    except AssetMetadataError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    except Exception:
        session.rollback()
        raise


@router.get("/assets/{asset_id}")
def agent_asset(
    asset_id: UUID,
    session: SessionDependency,
    _: Annotated[AgentPrincipal, scoped("assets:read")],
) -> AssetDetail:
    result = get_asset(session, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="资产不存在或已归档。")
    return result


@router.patch("/assets/{asset_id}")
def agent_update_asset(
    asset_id: UUID,
    payload: AssetUpdateRequest,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("metadata:write")],
) -> AssetSummary:
    try:
        result = update_asset(
            session,
            asset_id,
            payload,
            actor=principal.user,
            credential_name=principal.token.name,
        )
        session.commit()
        return result
    except AssetNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="资产不存在或已归档。") from None
    except AssetMetadataError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    except Exception:
        session.rollback()
        raise


@router.get("/assets/{asset_id}/citation/bibtex")
def agent_publication_citation(
    asset_id: UUID,
    session: SessionDependency,
    _: Annotated[AgentPrincipal, scoped("citations:export")],
) -> PublicationCitationResponse:
    asset = get_asset(session, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在或已归档。")
    try:
        return build_publication_citation(asset)
    except PublicationCitationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
def agent_create_upload(
    payload: AgentUploadCreateRequest,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("files:upload")],
) -> AgentUploadCreateResponse:
    try:
        result = create_agent_upload(
            session,
            payload.asset_id,
            payload.target_subdirectory,
            actor=principal.user,
            access_token=principal.token,
        )
        session.commit()
        return result
    except UploadCommandError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise


@router.put("/uploads/{upload_id}/files/{relative_path:path}")
async def agent_upload_file(
    upload_id: UUID,
    relative_path: str,
    request: Request,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("files:upload")],
    x_sage_upload_token: Annotated[str | None, Header()] = None,
    x_sage_content_sha256: Annotated[str | None, Header()] = None,
) -> AgentUploadedFileResponse:
    if not x_sage_upload_token:
        raise HTTPException(status_code=401, detail="缺少 X-Sage-Upload-Token。")
    content_length = request.headers.get("content-length")
    try:
        declared_size = int(content_length) if content_length else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Content-Length 无效。") from None
    if declared_size is not None and declared_size > settings.agent_upload_max_bytes:
        raise HTTPException(status_code=413, detail="上传文件超过服务器限制。")
    expected_checksum = (x_sage_content_sha256 or "").strip().lower()
    if expected_checksum and (
        len(expected_checksum) != 64
        or any(character not in "0123456789abcdef" for character in expected_checksum)
    ):
        raise HTTPException(status_code=400, detail="X-Sage-Content-SHA256 格式无效。")
    try:
        validate_agent_upload(
            session,
            upload_id,
            x_sage_upload_token,
            principal.user,
            principal.token,
        )
        destination, parts_directory = staged_upload_destination(
            settings.storage_root, upload_id, relative_path
        )
        with temporary_upload_path(parts_directory) as temporary_file:
            received = 0
            digest = hashlib.sha256()
            with temporary_file.open("xb") as output:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > settings.agent_upload_max_bytes:
                        raise UploadTooLargeError("上传文件超过服务器限制。")
                    digest.update(chunk)
                    output.write(chunk)
            if received == 0:
                raise UploadContentError("不能上传空文件。")
            checksum_sha256 = digest.hexdigest()
            if expected_checksum and not hmac.compare_digest(
                expected_checksum, checksum_sha256
            ):
                raise UploadContentError("文件 SHA-256 校验失败，请重新上传。")
            result = complete_agent_file_upload(
                upload_id,
                relative_path,
                temporary_file,
                destination,
                checksum_sha256,
            )
        session.commit()
        return result
    except UploadTicketError as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from None
    except UploadTooLargeError as error:
        session.rollback()
        raise HTTPException(status_code=413, detail=str(error)) from None
    except (UploadContentError, UploadConflictError, ValueError) as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise


@router.post("/uploads/{upload_id}/finalize")
def agent_finalize_upload(
    upload_id: UUID,
    payload: UploadFinalizeRequest,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("archive:finalize")],
) -> UploadFinalizeResponse:
    try:
        return finalize_upload(
            session,
            settings.storage_root,
            upload_id,
            payload.upload_token,
            actor=principal.user,
            access_token=principal.token,
        )
    except UploadTicketError as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from None
    except (UploadNotReadyError, UploadContentError, UploadConflictError) as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise
