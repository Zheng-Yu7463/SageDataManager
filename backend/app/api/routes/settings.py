import hashlib
import hmac
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response, status

from app.api.dependencies import AdminDependency, InstanceOwnerDependency, SessionDependency
from app.core.config import settings
from app.domain.schemas import (
    InstanceBrandingResponse,
    InstanceBrandingUpdateRequest,
    SystemUpdateApplyRequest,
    SystemUpdateStatus,
)
from app.services.branding import (
    MAX_LOGO_BYTES,
    BrandingLogoError,
    branding_response,
    get_branding_record,
    remove_branding_logo,
    update_branding,
    update_branding_logo,
)
from app.services.security import verify_password
from app.services.system_updates import (
    UpdateAgentRequestError,
    UpdateAgentUnavailableError,
    disabled_update_status,
    request_update_agent,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/branding")
def get_branding(session: SessionDependency) -> InstanceBrandingResponse:
    return branding_response(session)


@router.patch("/branding")
def patch_branding(
    payload: InstanceBrandingUpdateRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> InstanceBrandingResponse:
    try:
        result = update_branding(session, payload, actor=current_user)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


@router.get("/branding/logo/{content_digest}")
def get_branding_logo(
    content_digest: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    session: SessionDependency,
) -> Response:
    record = get_branding_record(session)
    if not record or not record.logo_data or not record.logo_mime_type:
        raise HTTPException(status_code=404, detail="尚未设置自定义 Logo。")
    actual_digest = hashlib.sha256(record.logo_data).hexdigest()
    if not hmac.compare_digest(content_digest, actual_digest):
        raise HTTPException(status_code=404, detail="Logo 内容版本不存在。")
    return Response(
        content=record.logo_data,
        media_type=record.logo_mime_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{actual_digest}"',
        },
    )


@router.put("/branding/logo")
async def put_branding_logo(
    request: Request,
    session: SessionDependency,
    current_user: AdminDependency,
) -> InstanceBrandingResponse:
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Logo 文件必须小于 1 MB。",
        )
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_LOGO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Logo 文件必须小于 1 MB。",
            )
    try:
        result = update_branding_logo(
            session,
            bytes(content),
            request.headers.get("content-type", ""),
            actor=current_user,
        )
        session.commit()
        return result
    except BrandingLogoError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise


@router.delete("/branding/logo")
def delete_branding_logo(
    session: SessionDependency,
    current_user: AdminDependency,
) -> InstanceBrandingResponse:
    try:
        result = remove_branding_logo(session, actor=current_user)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def _update_agent_response(method: str, path: str) -> SystemUpdateStatus:
    try:
        return SystemUpdateStatus.model_validate(request_update_agent(method, path))
    except UpdateAgentUnavailableError as error:
        if method != "GET":
            raise HTTPException(status_code=503, detail=str(error)) from None
        return SystemUpdateStatus.model_validate(disabled_update_status(str(error)))
    except UpdateAgentRequestError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@router.get("/system-update")
def get_system_update_status(current_user: AdminDependency) -> SystemUpdateStatus:
    return _update_agent_response("GET", "/v1/status")


@router.post("/system-update/check")
def check_system_update(current_user: AdminDependency) -> SystemUpdateStatus:
    return _update_agent_response("POST", "/v1/check")


@router.post("/system-update/apply", status_code=status.HTTP_202_ACCEPTED)
def apply_system_update(
    payload: SystemUpdateApplyRequest,
    current_user: InstanceOwnerDependency,
) -> SystemUpdateStatus:
    password_matches = bool(
        current_user.password_hash and verify_password(payload.password, current_user.password_hash)
    )
    legacy_matches = bool(
        not current_user.password_hash
        and settings.fixed_account_password
        and hmac.compare_digest(payload.password, settings.fixed_account_password)
    )
    if not password_matches and not legacy_matches:
        raise HTTPException(status_code=403, detail="当前账号密码不正确。")
    return _update_agent_response("POST", "/v1/update")
