import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Text, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import AssetType, Visibility
from app.domain.models import Activity, Asset, AssetRelation, AssetVersion, FileRecord, Tag, User
from app.domain.schemas import (
    ActivitySummary,
    AssetCreateRequest,
    AssetDetail,
    AssetRelationCreateRequest,
    AssetSummary,
    AssetUpdateRequest,
    AssetVersionCreateRequest,
    AssetVersionSummary,
    BatchAssetImportRequest,
    FileSummary,
    PaperCatalogueFacets,
    RelatedAssetSummary,
    UploadDirectoryOption,
    normalized_asset_details,
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


class AssetVersionError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class BatchAssetImportError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class AssetMetadataError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _normalized_title(title: str) -> str:
    return "".join(character.lower() for character in title if character.isalnum())


def _paper_identity(details: dict, title: str) -> tuple[str, str, str]:
    doi = str(details.get("doi", "")).removeprefix("https://doi.org/").strip().lower()
    source_id = str(details.get("source_id", "")).strip().lower()
    authors = details.get("authors") or []
    first_author = str(authors[0]).strip().lower() if authors else ""
    return doi, source_id, f"{_normalized_title(title)}::{first_author}"


def _papers_are_duplicates(
    first: tuple[str, str, str], second: tuple[str, str, str]
) -> bool:
    return bool(
        (first[0] and first[0] == second[0])
        or (first[1] and first[1] == second[1])
        or first[2] == second[2]
    )


def _find_duplicate_paper(
    session: Session, *, title: str, details: dict, exclude_asset_id: UUID | None = None
) -> Asset | None:
    identity = _paper_identity(details, title)
    statement = select(Asset).where(Asset.type == AssetType.PAPER)
    if exclude_asset_id:
        statement = statement.where(Asset.id != exclude_asset_id)
    for asset in session.scalars(statement):
        candidate = _paper_identity(asset.details, asset.title)
        if _papers_are_duplicates(identity, candidate):
            return asset
    return None


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
    if payload.type == AssetType.PAPER and _find_duplicate_paper(
        session, title=payload.title, details=payload.details
    ):
        raise AssetMetadataError("该论文已经收录，请按官方来源标识更新现有记录。")

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


def import_assets(
    session: Session, payload: BatchAssetImportRequest, *, actor: User
) -> list[AssetSummary]:
    slugs = [asset.slug for asset in payload.assets]
    if len(set(slugs)) != len(slugs):
        raise BatchAssetImportError("导入内容中含有重复的 slug。")
    existing = set(session.scalars(select(Asset.slug).where(Asset.slug.in_(slugs))).all())
    if existing:
        raise BatchAssetImportError(f"以下 slug 已存在：{', '.join(sorted(existing))}")
    paper_identities = [
        _paper_identity(item.details, item.title)
        for item in payload.assets
        if item.type == AssetType.PAPER
    ]
    for index, identity in enumerate(paper_identities):
        if any(_papers_are_duplicates(identity, other) for other in paper_identities[:index]):
            raise BatchAssetImportError("导入内容中含有重复论文。")
    for item in payload.assets:
        if item.type == AssetType.PAPER and _find_duplicate_paper(
            session, title=item.title, details=item.details
        ):
            raise BatchAssetImportError(f"论文已收录：{item.title}")
    return [create_asset(session, item, actor=actor) for item in payload.assets]


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
    next_title = payload.title.strip() if payload.title is not None else asset.title
    try:
        next_details = (
            normalized_asset_details(asset.type, payload.details)
            if payload.details is not None
            else asset.details
        )
    except ValueError as error:
        raise AssetMetadataError("论文元数据不完整或格式无效。") from error
    if asset.type == AssetType.PAPER and _find_duplicate_paper(
        session,
        title=next_title,
        details=next_details,
        exclude_asset_id=asset.id,
    ):
        raise AssetMetadataError("该论文已经收录，请检查 DOI 或官方来源标识。")
    if payload.title is not None:
        asset.title = next_title
    if payload.summary is not None:
        asset.summary = payload.summary.strip()
    if payload.status is not None:
        asset.status = payload.status.strip()
    if payload.visibility is not None:
        asset.visibility = payload.visibility
    if payload.tags is not None:
        asset.tags = _tags(session, payload.tags)
    if payload.details is not None:
        asset.details = next_details
    session.add(
        Activity(
            asset=asset, actor=actor, action="updated_metadata", description="更新了资产基础信息"
        )
    )
    session.flush()
    return asset_summary(asset)


def add_asset_version(
    session: Session, asset_id: UUID, payload: AssetVersionCreateRequest, *, actor: User
) -> AssetVersionSummary:
    asset = session.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.archived_at.is_(None))
        .options(selectinload(Asset.versions))
    )
    if not asset:
        raise AssetNotFoundError
    version_name = payload.version.strip()
    if not version_name:
        raise AssetVersionError("版本号不能为空。")
    if any(version.version == version_name for version in asset.versions):
        raise AssetVersionError("该版本号已经登记。")
    if payload.make_current:
        for version in asset.versions:
            version.is_current = False
    version = AssetVersion(
        version=version_name,
        release_notes=payload.release_notes.strip(),
        is_current=payload.make_current,
    )
    asset.versions.append(version)
    session.flush()
    current_label = "（设为当前版本）" if payload.make_current else ""
    session.add(
        Activity(
            asset=asset,
            actor=actor,
            action="added_version",
            description=f"登记了版本 {version_name}{current_label}",
        )
    )
    return AssetVersionSummary.model_validate(version)


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
    status: str | None,
    visibility: Visibility | None,
    has_files: bool | None,
    venue: str | None,
    year: int | None,
    page_size: int,
) -> tuple[list[AssetSummary], int]:
    filters = _asset_filters(
        asset_type=asset_type,
        query=query,
        status=status,
        visibility=visibility,
        has_files=has_files,
        venue=venue,
        year=year,
    )

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
    )
    if search_terms := _search_terms(query):
        statement = statement.order_by(
            _search_relevance(query or "", search_terms).desc(),
            Asset.updated_at.desc(),
            Asset.id.asc(),
        )
    else:
        statement = statement.order_by(Asset.updated_at.desc(), Asset.id.asc())
    statement = statement.offset((page - 1) * page_size).limit(page_size)
    return [asset_summary(item) for item in session.scalars(statement).all()], total


def _search_terms(query: str | None) -> list[str]:
    if not query:
        return []
    tokens = [quoted or plain for quoted, plain in re.findall(r'"([^"]+)"|(\S+)', query)]
    terms: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized = " ".join(token.split())
        identity = normalized.casefold()
        if normalized and identity not in seen:
            seen.add(identity)
            terms.append(normalized)
    return terms[:12]


def _contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _prefix_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def _paper_search_fields():
    return (
        cast(Asset.details["authors"], Text),
        cast(Asset.details["venue"], Text),
        cast(Asset.details["year"], Text),
        cast(Asset.details["track"], Text),
        cast(Asset.details["source_id"], Text),
        cast(Asset.details["doi"], Text),
        cast(Asset.details["booktitle"], Text),
        cast(Asset.details["publisher"], Text),
    )


def _paper_metadata_matches(pattern: str):
    return and_(
        Asset.type == AssetType.PAPER,
        or_(*(field.ilike(pattern, escape="\\") for field in _paper_search_fields())),
    )


def _term_matches(term: str):
    pattern = _contains_pattern(term)
    return or_(
        Asset.title.ilike(pattern, escape="\\"),
        Asset.summary.ilike(pattern, escape="\\"),
        Asset.tags.any(Tag.name.ilike(pattern, escape="\\")),
        Asset.owner.has(User.name.ilike(pattern, escape="\\")),
        Asset.files.any(FileRecord.file_name.ilike(pattern, escape="\\")),
        _paper_metadata_matches(pattern),
    )


def _search_relevance(query: str, terms: list[str]):
    phrase = " ".join(query.strip().strip('"').split())
    phrase_pattern = _contains_pattern(phrase)
    score = case((func.lower(Asset.title) == phrase.casefold(), 160), else_=0)
    score += case((Asset.title.ilike(_prefix_pattern(phrase), escape="\\"), 100), else_=0)
    score += case((Asset.title.ilike(phrase_pattern, escape="\\"), 80), else_=0)
    score += case(
        (Asset.tags.any(Tag.name.ilike(phrase_pattern, escape="\\")), 65), else_=0
    )
    score += case(
        (Asset.owner.has(User.name.ilike(phrase_pattern, escape="\\")), 55), else_=0
    )
    score += case((Asset.summary.ilike(phrase_pattern, escape="\\"), 40), else_=0)
    score += case((_paper_metadata_matches(phrase_pattern), 35), else_=0)
    score += case(
        (Asset.files.any(FileRecord.file_name.ilike(phrase_pattern, escape="\\")), 30),
        else_=0,
    )
    for term in terms:
        pattern = _contains_pattern(term)
        score += case((Asset.title.ilike(pattern, escape="\\"), 45), else_=0)
        score += case((Asset.tags.any(Tag.name.ilike(pattern, escape="\\")), 35), else_=0)
        score += case((Asset.owner.has(User.name.ilike(pattern, escape="\\")), 20), else_=0)
        score += case((Asset.summary.ilike(pattern, escape="\\"), 12), else_=0)
        score += case(
            (Asset.files.any(FileRecord.file_name.ilike(pattern, escape="\\")), 10), else_=0
        )
        score += case((_paper_metadata_matches(pattern), 8), else_=0)
    return score


def _asset_filters(
    *,
    asset_type: AssetType | None,
    query: str | None,
    status: str | None,
    visibility: Visibility | None,
    has_files: bool | None,
    venue: str | None,
    year: int | None,
) -> list:
    filters = [Asset.archived_at.is_(None)]
    if asset_type:
        filters.append(Asset.type == asset_type)
    if status and status.strip():
        filters.append(Asset.status == status.strip())
    if visibility:
        filters.append(Asset.visibility == visibility)
    if has_files is True:
        filters.append(Asset.files.any())
    elif has_files is False:
        filters.append(~Asset.files.any())
    if venue and venue.strip():
        filters.extend(
            [Asset.type == AssetType.PAPER, Asset.details["venue"].as_string() == venue.strip()]
        )
    if year is not None:
        filters.extend([Asset.type == AssetType.PAPER, Asset.details["year"].as_integer() == year])
    if terms := _search_terms(query):
        filters.append(and_(*(_term_matches(term) for term in terms)))

    return filters


def list_papers_for_citation_export(
    session: Session,
    *,
    query: str | None,
    status: str | None,
    visibility: Visibility | None,
    has_files: bool | None,
    venue: str | None,
    year: int | None,
) -> list[AssetSummary]:
    filters = _asset_filters(
        asset_type=AssetType.PAPER,
        query=query,
        status=status,
        visibility=visibility,
        has_files=has_files,
        venue=venue,
        year=year,
    )
    statement = (
        select(Asset)
        .where(*filters)
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
            selectinload(Asset.files),
        )
        .order_by(
            Asset.details["year"].as_integer().desc(),
            Asset.title.asc(),
            Asset.id.asc(),
        )
    )
    return [asset_summary(item) for item in session.scalars(statement).all()]


def list_paper_catalogue_facets(session: Session) -> PaperCatalogueFacets:
    statement = select(Asset.details).where(
        Asset.type == AssetType.PAPER,
        Asset.archived_at.is_(None),
    )
    details = session.scalars(statement).all()
    venues = sorted(
        {str(item["venue"]).strip() for item in details if str(item.get("venue", "")).strip()},
        key=str.casefold,
    )
    years = sorted(
        {int(item["year"]) for item in details if isinstance(item.get("year"), int)},
        reverse=True,
    )
    return PaperCatalogueFacets(venues=venues, years=years)


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
