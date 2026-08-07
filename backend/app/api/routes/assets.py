from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import AdminDependency
from app.db.session import get_session
from app.domain.enums import AssetType
from app.domain.schemas import (
    AssetCreateRequest,
    AssetListResponse,
    AssetSummary,
    AssetUpdateRequest,
)
from app.services.assets import (
    AssetNotFoundError,
    AssetSlugConflictError,
    archive_asset,
    create_asset,
    get_asset,
    list_archived_assets,
    list_assets,
    restore_asset,
    update_asset,
)

router = APIRouter(prefix="/assets", tags=["assets"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("")
def assets(
    session: SessionDependency,
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
        page_size=page_size,
    )

    return AssetListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", status_code=status.HTTP_201_CREATED)
def create(
    payload: AssetCreateRequest, session: SessionDependency, current_user: AdminDependency
) -> AssetSummary:
    try:
        result = create_asset(session, payload, actor=current_user)
        session.commit()
        return result
    except AssetSlugConflictError:
        session.rollback()
        raise HTTPException(status_code=409, detail="资产标识已存在，请使用另一个 slug。") from None
    except Exception:
        session.rollback()
        raise


@router.get("/archived")
def archived_assets(session: SessionDependency, _: AdminDependency) -> list[AssetSummary]:
    return list_archived_assets(session)


@router.patch("/{asset_id}")
def update(
    asset_id: UUID,
    payload: AssetUpdateRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> AssetSummary:
    try:
        result = update_asset(session, asset_id, payload, actor=current_user)
        session.commit()
        return result
    except AssetNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="资产不存在或已归档。") from None
    except Exception:
        session.rollback()
        raise


@router.post("/{asset_id}/archive")
def archive(
    asset_id: UUID, session: SessionDependency, current_user: AdminDependency
) -> AssetSummary:
    try:
        result = archive_asset(session, asset_id, actor=current_user)
        session.commit()
        return result
    except AssetNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="资产不存在或已归档。") from None
    except Exception:
        session.rollback()
        raise


@router.post("/{asset_id}/restore")
def restore(
    asset_id: UUID, session: SessionDependency, current_user: AdminDependency
) -> AssetSummary:
    try:
        result = restore_asset(session, asset_id, actor=current_user)
        session.commit()
        return result
    except AssetNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="已归档资产不存在。") from None
    except Exception:
        session.rollback()
        raise


@router.get("/{asset_id}")
def asset(asset_id: UUID, session: SessionDependency) -> AssetSummary:
    result = get_asset(session, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="Asset not found")
    return result
