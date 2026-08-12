from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request, Response

from app.api.dependencies import AdminDependency, SessionDependency
from app.domain.schemas import InstanceBrandingResponse, InstanceBrandingUpdateRequest
from app.services.branding import (
    BrandingLogoError,
    branding_response,
    get_branding_record,
    remove_branding_logo,
    update_branding,
    update_branding_logo,
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
    result = update_branding(session, payload, actor=current_user)
    session.commit()
    return result


@router.get("/branding/logo")
def get_branding_logo(session: SessionDependency) -> Response:
    record = get_branding_record(session)
    if not record or not record.logo_data or not record.logo_mime_type:
        raise HTTPException(status_code=404, detail="尚未设置自定义 Logo。")
    return Response(
        content=record.logo_data,
        media_type=record.logo_mime_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.put("/branding/logo")
async def put_branding_logo(
    request: Request,
    session: SessionDependency,
    current_user: AdminDependency,
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
) -> InstanceBrandingResponse:
    try:
        result = update_branding_logo(
            session,
            content,
            request.headers.get("content-type", ""),
            actor=current_user,
        )
        session.commit()
        return result
    except BrandingLogoError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.delete("/branding/logo")
def delete_branding_logo(
    session: SessionDependency,
    current_user: AdminDependency,
) -> InstanceBrandingResponse:
    result = remove_branding_logo(session, actor=current_user)
    session.commit()
    return result
