from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import AdminDependency, require_admin
from app.db.session import get_session
from app.domain.activity import ActivityOperationRole
from app.domain.enums import AssetType, HealthStatus
from app.domain.models import Activity, Asset, FileRecord, Tag, asset_tags
from app.domain.schemas import ActivityListResponse, DashboardSummary
from app.services.activities import (
    activity_facets,
    activity_summary,
    recent_activity_summaries,
)
from app.services.assets import asset_summaries

router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(require_admin)],
)
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("")
def dashboard(session: SessionDependency) -> DashboardSummary:
    count_rows = session.execute(
        select(Asset.type, func.count(Asset.id))
        .where(Asset.archived_at.is_(None))
        .group_by(Asset.type)
    ).all()
    counts = {asset_type: 0 for asset_type in AssetType}
    counts.update(dict(count_rows))

    storage, healthy, missing = session.execute(
        select(
            func.coalesce(func.sum(FileRecord.file_size), 0),
            func.count(FileRecord.id).filter(FileRecord.health_status == HealthStatus.HEALTHY),
            func.count(FileRecord.id).filter(FileRecord.health_status == HealthStatus.MISSING),
        )
    ).one()

    recent_assets = session.scalars(
        select(Asset)
        .where(Asset.archived_at.is_(None))
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
        )
        .order_by(Asset.updated_at.desc())
        .limit(5)
    ).all()

    activity_summaries = recent_activity_summaries(session, limit=6, primary_only=True)

    popular_tags = session.execute(
        select(Tag.name, func.count(asset_tags.c.asset_id).label("usage_count"))
        .join(asset_tags, asset_tags.c.tag_id == Tag.id)
        .join(Asset, Asset.id == asset_tags.c.asset_id)
        .where(Asset.archived_at.is_(None))
        .group_by(Tag.id)
        .order_by(func.count(asset_tags.c.asset_id).desc(), Tag.name)
        .limit(10)
    ).all()

    return DashboardSummary(
        counts=counts,
        total_storage_bytes=storage,
        healthy_files=healthy,
        missing_files=missing,
        recent_assets=asset_summaries(session, list(recent_assets)),
        recent_activities=activity_summaries,
        popular_tags=[(name, count) for name, count in popular_tags],
    )


@router.get("/activities")
def activities(
    session: SessionDependency,
    _: AdminDependency,
    action: str | None = Query(default=None, max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> ActivityListResponse:
    filters = [Activity.operation_role != ActivityOperationRole.TARGET]
    if action:
        filters.append(Activity.action == action)
    total = session.scalar(select(func.count()).select_from(Activity).where(*filters)) or 0
    rows = session.scalars(
        select(Activity)
        .where(*filters)
        .order_by(Activity.created_at.desc(), Activity.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ActivityListResponse(
        items=[activity_summary(item) for item in rows],
        facets=activity_facets(session),
        total=total,
        page=page,
        page_size=page_size,
    )
