from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import AssetType
from app.domain.models import Activity, Asset, AssetRelation, AssetVersion, Tag, User
from app.domain.schemas import (
    ActivitySummary,
    AssetCreateRequest,
    AssetDetail,
    AssetSummary,
    AssetVersionSummary,
    FileSummary,
    RelatedAssetSummary,
)


class AssetSlugConflictError(Exception):
    pass


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


def create_asset(session: Session, payload: AssetCreateRequest) -> AssetSummary:
    if session.scalar(select(Asset.id).where(Asset.slug == payload.slug)):
        raise AssetSlugConflictError

    owner_email = payload.owner_email.strip().lower()
    owner = session.scalar(select(User).where(User.email == owner_email))
    if not owner:
        owner = User(name=payload.owner_name.strip(), email=owner_email)
        session.add(owner)

    tag_names = sorted({tag.strip() for tag in payload.tags if tag.strip()})
    existing_tags = {
        tag.name: tag for tag in session.scalars(select(Tag).where(Tag.name.in_(tag_names))).all()
    }
    tags = [existing_tags.get(name) or Tag(name=name) for name in tag_names]
    asset = Asset(
        type=payload.type,
        slug=payload.slug,
        title=payload.title.strip(),
        summary=payload.summary.strip(),
        status=payload.status.strip(),
        visibility=payload.visibility,
        owner=owner,
        details=payload.details,
        tags=tags,
    )
    if payload.version and payload.version.strip():
        asset.versions.append(AssetVersion(version=payload.version.strip(), is_current=True))
    session.add(asset)
    session.flush()
    session.add(
        Activity(
            asset=asset,
            actor=owner,
            action="created",
            description=f"登记了{asset.title}",
        )
    )
    session.flush()
    return asset_summary(asset)


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


def get_asset(session: Session, asset_id: UUID) -> AssetDetail | None:
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
    if not asset:
        return None

    relations = session.scalars(
        select(AssetRelation).where(
            or_(
                AssetRelation.source_asset_id == asset.id,
                AssetRelation.target_asset_id == asset.id,
            )
        )
    ).all()
    related_ids = {
        relation.target_asset_id
        if relation.source_asset_id == asset.id
        else relation.source_asset_id
        for relation in relations
    }
    related_by_id = (
        {
            item.id: item
            for item in session.scalars(
                select(Asset).where(Asset.id.in_(related_ids), Asset.archived_at.is_(None))
            ).all()
        }
        if related_ids
        else {}
    )
    activities = session.scalars(
        select(Activity)
        .where(Activity.asset_id == asset.id)
        .options(selectinload(Activity.actor))
        .order_by(Activity.created_at.desc())
        .limit(20)
    ).all()

    summary = asset_summary(asset)
    return AssetDetail(
        **summary.model_dump(),
        versions=[AssetVersionSummary.model_validate(version) for version in asset.versions],
        files=[FileSummary.model_validate(file) for file in asset.files],
        related_assets=[
            RelatedAssetSummary(
                id=related.id,
                type=related.type,
                slug=related.slug,
                title=related.title,
                relation_type=relation.relation_type,
            )
            for relation in relations
            if (
                related := related_by_id.get(
                    relation.target_asset_id
                    if relation.source_asset_id == asset.id
                    else relation.source_asset_id
                )
            )
        ],
        recent_activities=[
            ActivitySummary(
                id=activity.id,
                asset_id=asset.id,
                asset_title=asset.title,
                asset_type=asset.type,
                actor_name=activity.actor.name if activity.actor else None,
                action=activity.action,
                description=activity.description,
                created_at=activity.created_at,
            )
            for activity in activities
        ],
    )
