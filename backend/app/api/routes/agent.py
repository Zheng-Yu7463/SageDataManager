from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import AgentPrincipal, require_agent, require_agent_scope
from app.api.routes.files import OpenFileResponse
from app.core.config import settings
from app.db.session import get_session
from app.domain.enums import AssetType
from app.domain.schemas import (
    AgentAssetListItem,
    AgentAssetListResponse,
    AgentIdentityResponse,
    AgentUploadCancelResponse,
    AgentUploadCreateRequest,
    AgentUploadCreateResponse,
    AgentUploadedFileResponse,
    AgentUploadStatusResponse,
    AssetCreateRequest,
    AssetDetail,
    AssetSummary,
    AssetUpdateRequest,
    PublicationCitationResponse,
    UploadFinalizeRequest,
    UploadFinalizeResponse,
)
from app.services.access_tokens import record_access_token_use
from app.services.assets import (
    AssetConflictError,
    AssetMetadataError,
    AssetNotFoundError,
    AssetSlugConflictError,
    create_asset,
    get_asset,
    list_assets,
    update_asset,
)
from app.services.citations import PublicationCitationError, build_publication_citation
from app.services.file_access import (
    FileNotFoundError,
    FilePreviewUnavailableError,
    FileUnavailableError,
    open_file_delivery,
)
from app.services.transfers import (
    UploadCommandError,
    UploadConflictError,
    UploadContentError,
    UploadNotReadyError,
    UploadTicketError,
    UploadTooLargeError,
    agent_upload_status,
    cancel_agent_upload,
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


@router.get("/me", summary="Read Agent identity and granted scopes")
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


@router.get(
    "/assets",
    summary="Search the asset catalogue",
    description="Returns compact records. Read a specific asset for full metadata and files.",
)
def agent_assets(
    session: SessionDependency,
    _: Annotated[AgentPrincipal, scoped("assets:read")],
    asset_type: AssetType | None = None,
    query: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> AgentAssetListResponse:
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
    compact_items = [
        AgentAssetListItem(
            id=item.id,
            type=item.type,
            slug=item.slug,
            title=item.title,
            status=item.status,
            visibility=item.visibility,
            tags=item.tags,
            source_id=(
                item.details.get("source_id")
                if isinstance(item.details.get("source_id"), str)
                else None
            ),
            file_count=item.file_count,
            default_upload_directory=item.default_upload_directory,
            updated_at=item.updated_at,
        )
        for item in items
    ]
    return AgentAssetListResponse(
        items=compact_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/assets",
    status_code=status.HTTP_201_CREATED,
    summary="Create an asset metadata record",
    responses={409: {"description": "Slug or publication identity conflict"}},
)
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


@router.get("/assets/{asset_id}", summary="Read full asset metadata and file index")
def agent_asset(
    asset_id: UUID,
    session: SessionDependency,
    _: Annotated[AgentPrincipal, scoped("assets:read")],
) -> AssetDetail:
    result = get_asset(session, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="资产不存在或已归档。")
    return result


@router.head("/files/{file_id}/content", include_in_schema=False)
@router.get(
    "/files/{file_id}/content",
    summary="Read or download an indexed file",
    description="Streams the indexed file directly and supports HTTP Range requests.",
    responses={
        404: {"description": "File or active asset not found"},
        409: {"description": "File is unavailable or cannot be previewed"},
    },
)
def agent_file_content(
    file_id: UUID,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("files:read")],
    mode: Annotated[Literal["download", "preview"], Query()] = "download",
) -> Response:
    delivery = None
    try:
        delivery = open_file_delivery(
            session,
            settings.storage_root,
            file_id,
            mode,
            actor=principal.user,
            credential_name=principal.token.name,
        )
        session.commit()
    except FileNotFoundError:
        if delivery:
            delivery.close()
        session.rollback()
        raise HTTPException(
            status_code=404,
            detail="文件不存在或所属资产已归档。",
        ) from None
    except FilePreviewUnavailableError:
        if delivery:
            delivery.close()
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="此文件类型暂不支持预览，请使用 download 模式。",
        ) from None
    except FileUnavailableError:
        if delivery:
            delivery.close()
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="文件当前不可用，请先重新扫描归档。",
        ) from None
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


@router.patch(
    "/assets/{asset_id}",
    summary="Update asset metadata",
    description="Requires X-Sage-Asset-Revision from the latest asset detail response.",
    responses={409: {"description": "Metadata conflict or stale revision"}},
)
def agent_update_asset(
    asset_id: UUID,
    payload: AssetUpdateRequest,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("metadata:write")],
    x_sage_asset_revision: Annotated[datetime, Header(alias="X-Sage-Asset-Revision")],
) -> AssetSummary:
    try:
        result = update_asset(
            session,
            asset_id,
            payload,
            actor=principal.user,
            credential_name=principal.token.name,
            expected_revision=x_sage_asset_revision,
        )
        session.commit()
        return result
    except AssetConflictError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="资产已被其他操作更新，请重新读取详情后再提交。",
        ) from None
    except AssetNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="资产不存在或已归档。") from None
    except AssetMetadataError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    except Exception:
        session.rollback()
        raise


@router.get(
    "/assets/{asset_id}/citation/bibtex",
    summary="Export publication metadata as BibTeX",
    responses={409: {"description": "Asset has incomplete citation metadata"}},
)
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


@router.post(
    "/uploads",
    status_code=status.HTTP_201_CREATED,
    summary="Create an isolated direct-upload task",
    responses={409: {"description": "Asset or target directory is invalid"}},
)
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


@router.get(
    "/uploads/{upload_id}",
    summary="Read an upload task status",
    responses={403: {"description": "Upload token or access token mismatch"}},
)
def agent_get_upload_status(
    upload_id: UUID,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("files:upload")],
    x_sage_upload_token: Annotated[str | None, Header()] = None,
) -> AgentUploadStatusResponse:
    if not x_sage_upload_token:
        raise HTTPException(status_code=401, detail="缺少 X-Sage-Upload-Token。")
    try:
        return agent_upload_status(
            session,
            settings.storage_root,
            upload_id,
            x_sage_upload_token,
            actor=principal.user,
            access_token=principal.token,
        )
    except UploadTicketError as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from None
    except UploadContentError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None


@router.delete(
    "/uploads/{upload_id}",
    summary="Cancel an active upload task",
    responses={
        403: {"description": "Upload token or access token mismatch"},
        409: {"description": "Task completed or staging area unavailable"},
    },
)
def agent_cancel_upload(
    upload_id: UUID,
    session: SessionDependency,
    principal: Annotated[AgentPrincipal, scoped("files:upload")],
    x_sage_upload_token: Annotated[str | None, Header()] = None,
) -> AgentUploadCancelResponse:
    if not x_sage_upload_token:
        raise HTTPException(status_code=401, detail="缺少 X-Sage-Upload-Token。")
    try:
        result = cancel_agent_upload(
            session,
            settings.storage_root,
            upload_id,
            x_sage_upload_token,
            actor=principal.user,
            access_token=principal.token,
        )
        session.commit()
        return result
    except UploadTicketError as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from None
    except UploadContentError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise


@router.put(
    "/uploads/{upload_id}/files/{relative_path:path}",
    summary="Upload one file into an active task",
    description=(
        "URL-encode relative_path, send X-Sage-Upload-Token, and optionally send "
        "X-Sage-Content-SHA256. The maximum file size is 500 MB."
    ),
    responses={
        400: {"description": "Invalid request header"},
        401: {"description": "Missing upload token"},
        403: {"description": "Upload token or access token mismatch"},
        409: {"description": "Invalid path, checksum mismatch, or file conflict"},
        413: {"description": "File exceeds the configured size limit"},
    },
)
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


@router.post(
    "/uploads/{upload_id}/finalize",
    summary="Finalize an upload into the formal archive",
    description="Idempotent when retried with the same task and credentials.",
    responses={
        403: {"description": "Upload token or access token mismatch"},
        409: {"description": "Task not ready, duplicate content, or path conflict"},
    },
)
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
