from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import AssetType
from app.domain.models import Activity, Asset, AssetRelation, AssetVersion, Tag, User
from app.domain.schemas import (
    ActivitySummary,
    AssetCreateRequest,
    AssetDetail,
    AssetRelationCreateRequest,
    AssetSummary,
    AssetUpdateRequest,
    AssetVersionSummary,
    FileSummary,
    RelatedAssetSummary,
    UploadDirectoryOption,
)
from app.services.upload_directories import UPLOAD_DIRECTORY_OPTIONS


class AssetSlugConflictError(Exception):
    pass


class AssetNotFoundError(Exception):
    pass


class AssetRelationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def asset_summary(asset: Asset) -> AssetSummary:
    current_version = next((item.version for item in asset.versions if item.is_current), None)
    upload_directories = [
        UploadDirectoryOption(name=name, label=label)
        for name, label in UPLOAD_DIRECTORY_OPTIONS[asset.type]
    ]
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
        file_count=len(asset.files),
        upload_directories=upload_directories,
        default_upload_directory=upload_directories[0].name,
        updated_at=asset.updated_at,
    )


def _tags(session: Session, tag_values: list[str]) -> list[Tag]:
    names = sorted({tag.strip() for tag in tag_values if tag.strip()})
    existing = {
        tag.name: tag for tag in session.scalars(select(Tag).where(Tag.name.in_(names))).all()
    }
    return [existing.get(name) or Tag(name=name) for name in names]


def create_asset(
    session: Session, payload: AssetCreateRequest, *, actor: User | None = None
) -> AssetSummary:
    if session.scalar(select(Asset.id).where(Asset.slug == payload.slug)):
        raise AssetSlugConflictError

    owner = actor
    if payload.owner_email:
        owner_email = payload.owner_email.strip().lower()
        owner = session.scalar(select(User).where(User.email == owner_email))
        if not owner:
            owner = User(name=(payload.owner_name or "归档管理员").strip(), email=owner_email)
            session.add(owner)
    if not owner:
        owner = session.scalar(select(User).where(User.is_active.is_(True)).order_by(User.name))
    if not owner:
        owner = User(
            name=(payload.owner_name or "归档管理员").strip(), email="archive-admin@sage.lab"
        )
        session.add(owner)

    asset = Asset(
        type=payload.type,
        slug=payload.slug,
        title=payload.title.strip(),
        summary=payload.summary.strip(),
        status=payload.status.strip(),
        visibility=payload.visibility,
        owner=owner,
        details=payload.details,
        tags=_tags(session, payload.tags),
    )
    if payload.version and payload.version.strip():
        asset.versions.append(AssetVersion(version=payload.version.strip(), is_current=True))
    session.add(asset)
    session.flush()
    session.add(
        Activity(
            asset=asset,
            actor=actor or owner,
            action="created",
            description=f"登记了{asset.title}",
        )
    )
    session.flush()
    return asset_summary(asset)


def update_asset(
    session: Session, asset_id: UUID, payload: AssetUpdateRequest, *, actor: User
) -> AssetSummary:
    asset = session.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.archived_at.is_(None))
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
            selectinload(Asset.files),
        )
    )
    if not asset:
        raise AssetNotFoundError
    if payload.title is not None:
        asset.title = payload.title.strip()
    if payload.summary is not None:
        asset.summary = payload.summary.strip()
    if payload.status is not None:
        asset.status = payload.status.strip()
    if payload.visibility is not None:
        asset.visibility = payload.visibility
    if payload.tags is not None:
        asset.tags = _tags(session, payload.tags)
    if payload.details is not None:
        asset.details = payload.details
    session.add(
        Activity(
            asset=asset, actor=actor, action="updated_metadata", description="更新了资产基础信息"
        )
    )
    session.flush()
    return asset_summary(asset)


def archive_asset(session: Session, asset_id: UUID, *, actor: User) -> AssetSummary:
    asset = session.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.archived_at.is_(None))
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
            selectinload(Asset.files),
        )
    )
    if not asset:
        raise AssetNotFoundError
    asset.archived_at = datetime.now(UTC)
    session.add(Activity(asset=asset, actor=actor, action="archived", description="归档了该资产"))
    session.flush()
    return asset_summary(asset)


def restore_asset(session: Session, asset_id: UUID, *, actor: User) -> AssetSummary:
    asset = session.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.archived_at.is_not(None))
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
            selectinload(Asset.files),
        )
    )
    if not asset:
        raise AssetNotFoundError
    asset.archived_at = None
    session.add(Activity(asset=asset, actor=actor, action="restored", description="恢复了该资产"))
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


def list_archived_assets(session: Session) -> list[AssetSummary]:
    statement = (
        select(Asset)
        .where(Asset.archived_at.is_not(None))
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
            selectinload(Asset.files),
        )
        .order_by(Asset.archived_at.desc())
    )
    return [asset_summary(asset) for asset in session.scalars(statement).all()]


def add_asset_relation(
    session: Session, asset_id: UUID, payload: AssetRelationCreateRequest, *, actor: User
) -> RelatedAssetSummary:
    if asset_id == payload.target_asset_id:
        raise AssetRelationError("资产不能关联到自身。")
    assets = session.scalars(
        select(Asset).where(
            Asset.id.in_([asset_id, payload.target_asset_id]), Asset.archived_at.is_(None)
        )
    ).all()
    asset_by_id = {asset.id: asset for asset in assets}
    source = asset_by_id.get(asset_id)
    target = asset_by_id.get(payload.target_asset_id)
    if not source or not target:
        raise AssetRelationError("关联资产不存在或已归档。")
    relation_type = payload.relation_type.strip()
    if not relation_type:
        raise AssetRelationError("关系类型不能为空。")
    existing = session.scalar(
        select(AssetRelation).where(
            AssetRelation.source_asset_id == source.id,
            AssetRelation.target_asset_id == target.id,
            AssetRelation.relation_type == relation_type,
        )
    )
    if existing:
        raise AssetRelationError("相同的关联已存在。")
    relation = AssetRelation(
        source_asset_id=source.id, target_asset_id=target.id, relation_type=relation_type
    )
    session.add(relation)
    session.flush()
    session.add(
        Activity(
            asset=source,
            actor=actor,
            action="linked_asset",
            description=f"关联了资产「{target.title}」：{relation_type}",
        )
    )
    return RelatedAssetSummary(
        relation_id=relation.id,
        id=target.id,
        type=target.type,
        slug=target.slug,
        title=target.title,
        relation_type=relation.relation_type,
    )


def remove_asset_relation(
    session: Session, asset_id: UUID, relation_id: UUID, *, actor: User
) -> None:
    relation = session.scalar(
        select(AssetRelation).where(
            AssetRelation.id == relation_id,
            or_(
                AssetRelation.source_asset_id == asset_id,
                AssetRelation.target_asset_id == asset_id,
            ),
        )
    )
    if not relation:
        raise AssetRelationError("关联不存在或不能从当前资产移除。")
    other_asset_id = (
        relation.target_asset_id
        if relation.source_asset_id == asset_id
        else relation.source_asset_id
    )
    target = session.get(Asset, other_asset_id)
    description = f"移除了与资产「{target.title if target else '已删除资产'}」的关联"
    session.delete(relation)
    session.add(
        Activity(asset_id=asset_id, actor=actor, action="unlinked_asset", description=description)
    )


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
                relation_id=relation.id,
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
