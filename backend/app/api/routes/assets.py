from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain.enums import AssetType
from app.domain.schemas import AssetCreateRequest, AssetListResponse, AssetSummary
from app.services.assets import AssetSlugConflictError, create_asset, get_asset, list_assets

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
def create(payload: AssetCreateRequest, session: SessionDependency) -> AssetSummary:
    try:
        result = create_asset(session, payload)
        session.commit()
        return result
    except AssetSlugConflictError:
        session.rollback()
        raise HTTPException(status_code=409, detail="资产标识已存在，请使用另一个 slug。") from None
    except Exception:
        session.rollback()
        raise


@router.get("/{asset_id}")
def asset(asset_id: UUID, session: SessionDependency) -> AssetSummary:
    result = get_asset(session, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="Asset not found")
    return result
