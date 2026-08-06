from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import AssetType
from app.domain.models import Asset
from app.domain.schemas import AssetSummary


def asset_summary(asset: Asset) -> AssetSummary:
    current_version = next((item.version for item in asset.versions if item.is_current), None)
    return AssetSummary(
        id=asset.id,
        type=asset.type,
        slug=asset.slug,
        title=asset.title,
        summary=asset.summary,
        status=asset.status,
        visibility=asset.visibility,
        owner=asset.owner,
        details=asset.details,
        tags=sorted(tag.name for tag in asset.tags),
        current_version=current_version,
        total_size=sum(file.file_size for file in asset.files),
        updated_at=asset.updated_at,
    )


def list_assets(
    session: Session,
    *,
    asset_type: AssetType | None,
    query: str | None,
    page: int,
    page_size: int,
) -> tuple[list[AssetSummary], int]:
    filters = [Asset.archived_at.is_(None)]
    if asset_type:
        filters.append(Asset.type == asset_type)
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(or_(Asset.title.ilike(pattern), Asset.summary.ilike(pattern)))

    total = session.scalar(select(func.count()).select_from(Asset).where(*filters)) or 0
    statement = (
        select(Asset)
        .where(*filters)
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
            selectinload(Asset.files),
        )
        .order_by(Asset.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [asset_summary(item) for item in session.scalars(statement).all()], total


def get_asset(session: Session, asset_id: UUID) -> AssetSummary | None:
    statement = (
        select(Asset)
        .where(Asset.id == asset_id, Asset.archived_at.is_(None))
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
            selectinload(Asset.files),
        )
    )
    asset = session.scalar(statement)
    return asset_summary(asset) if asset else None
