from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.session import get_session
from app.domain.enums import AssetType, HealthStatus
from app.domain.models import Activity, Asset, FileRecord, Tag, asset_tags
from app.domain.schemas import ActivitySummary, DashboardSummary
from app.services.assets import asset_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
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
            selectinload(Asset.files),
        )
        .order_by(Asset.updated_at.desc())
        .limit(5)
    ).all()

    activities = session.scalars(
        select(Activity)
        .options(selectinload(Activity.asset), selectinload(Activity.actor))
        .order_by(Activity.created_at.desc())
        .limit(6)
    ).all()
    activity_summaries = [
        ActivitySummary(
            id=item.id,
            asset_id=item.asset_id,
            asset_title=item.asset.title if item.asset else None,
            asset_type=item.asset.type if item.asset else None,
            actor_name=item.actor.name if item.actor else None,
            action=item.action,
            description=item.description,
            created_at=item.created_at,
        )
        for item in activities
    ]

    popular_tags = session.execute(
        select(Tag.name, func.count(asset_tags.c.asset_id).label("usage_count"))
        .join(asset_tags, asset_tags.c.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(func.count(asset_tags.c.asset_id).desc(), Tag.name)
        .limit(10)
    ).all()

    return DashboardSummary(
        counts=counts,
        total_storage_bytes=storage,
        healthy_files=healthy,
        missing_files=missing,
        recent_assets=[asset_summary(item) for item in recent_assets],
        recent_activities=activity_summaries,
        popular_tags=[(name, count) for name, count in popular_tags],
    )
