import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Text, and_, case, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.constraints import violates_constraint
from app.domain.activity import ActivityAction, ActivityOperationRole
from app.domain.enums import AssetType, Visibility
from app.domain.models import Asset, AssetRelation, AssetVersion, FileRecord, Tag, User
from app.domain.schemas import (
    AssetChoiceSummary,
    AssetCreateRequest,
    AssetDetail,
    AssetRelationCreateRequest,
    AssetSummary,
    AssetUpdateRequest,
    AssetVersionCreateRequest,
    AssetVersionSummary,
    BatchAssetImportRequest,
    FileSummary,
    PublicationCatalogueFacets,
    RelatedAssetSummary,
    UploadDirectoryOption,
    normalized_asset_details,
)
from app.services.activities import recent_activity_summaries, record_activity
from app.services.paper_identity import (
    PublicationIdentityConflictError,
    publication_identities_match,
    publication_identity,
    resolve_publication,
    synchronize_publication_identity_keys,
)
from app.services.upload_directories import UPLOAD_DIRECTORY_OPTIONS


class AssetSlugConflictError(Exception):
    pass


class AssetNotFoundError(Exception):
    pass


class AssetConflictError(Exception):
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


ASSET_RELATION_UNIQUE_CONSTRAINT = "uq_asset_relations_identity"
ASSET_SLUG_UNIQUE_CONSTRAINTS = ("assets_slug_key", "ix_assets_slug")
PUBLICATION_IDENTITY_UNIQUE_CONSTRAINT = "uq_publication_identity_keys_identity"
PUBLICATION_ASSET_TYPES = (AssetType.PAPER, AssetType.LITERATURE)


def _translate_asset_integrity_error(error: IntegrityError) -> None:
    if any(
        violates_constraint(
            error,
            constraint_name,
            sqlite_columns=("assets.slug",),
        )
        for constraint_name in ASSET_SLUG_UNIQUE_CONSTRAINTS
    ):
        raise AssetSlugConflictError from error
    if violates_constraint(
        error,
        PUBLICATION_IDENTITY_UNIQUE_CONSTRAINT,
        sqlite_columns=("publication_identity_keys.kind", "publication_identity_keys.digest"),
    ):
        raise AssetMetadataError(
            "该出版物已经收录，请检查 DOI、官方来源标识或题名与首位作者。"
        ) from error
    raise error


def has_publication_metadata(asset_type: AssetType, details: dict) -> bool:
    return asset_type in PUBLICATION_ASSET_TYPES and bool(details.get("source_id"))


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


def _tag_names(tag_values: list[str]) -> list[str]:
    return sorted({tag.strip() for tag in tag_values if tag.strip()})


def _tags(session: Session, tag_values: list[str]) -> list[Tag]:
    names = _tag_names(tag_values)
    existing = {
        tag.name: tag for tag in session.scalars(select(Tag).where(Tag.name.in_(names))).all()
    }
    return [existing.get(name) or Tag(name=name) for name in names]


def create_asset(
    session: Session,
    payload: AssetCreateRequest,
    *,
    actor: User | None = None,
    credential_name: str | None = None,
) -> AssetSummary:
    if session.scalar(select(Asset.id).where(Asset.slug == payload.slug)):
        raise AssetSlugConflictError
    if has_publication_metadata(payload.type, payload.details):
        try:
            duplicate = resolve_publication(session, title=payload.title, details=payload.details)
        except PublicationIdentityConflictError as error:
            raise AssetMetadataError(str(error)) from error
        if duplicate:
            raise AssetMetadataError("该出版物已经收录，请按官方来源标识更新现有记录。")

    owner = actor
    if payload.owner_email and actor is None:
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
    synchronize_publication_identity_keys(asset)
    try:
        session.flush()
    except IntegrityError as error:
        _translate_asset_integrity_error(error)
    record_activity(
        session,
        asset=asset,
        actor=actor or owner,
        credential_name=credential_name,
        action=ActivityAction.CREATED,
        description=f"登记了{asset.title}",
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
    publication_identities = [
        publication_identity(item.title, item.details)
        for item in payload.assets
        if has_publication_metadata(item.type, item.details)
    ]
    for index, identity in enumerate(publication_identities):
        if any(
            publication_identities_match(identity, other)
            for other in publication_identities[:index]
        ):
            raise BatchAssetImportError("导入内容中含有重复出版物。")
    for item in payload.assets:
        if has_publication_metadata(item.type, item.details):
            try:
                duplicate = resolve_publication(session, title=item.title, details=item.details)
            except PublicationIdentityConflictError as error:
                raise BatchAssetImportError(str(error)) from error
            if duplicate:
                raise BatchAssetImportError(f"出版物已收录：{item.title}")
    try:
        return [create_asset(session, item, actor=actor) for item in payload.assets]
    except AssetSlugConflictError as error:
        raise BatchAssetImportError("导入过程中资产 slug 发生并发冲突。") from error
    except AssetMetadataError as error:
        raise BatchAssetImportError(error.message) from error


def update_asset(
    session: Session,
    asset_id: UUID,
    payload: AssetUpdateRequest,
    *,
    actor: User,
    credential_name: str | None = None,
    expected_revision: datetime | None = None,
) -> AssetSummary:
    asset = session.scalar(
        select(Asset)
        .where(Asset.id == asset_id, Asset.archived_at.is_(None))
        .with_for_update()
        .options(
            selectinload(Asset.owner),
            selectinload(Asset.tags),
            selectinload(Asset.versions),
            selectinload(Asset.files),
        )
    )
    if not asset:
        raise AssetNotFoundError
    if expected_revision is not None:
        stored_revision = asset.updated_at
        if stored_revision.tzinfo is None:
            stored_revision = stored_revision.replace(tzinfo=UTC)
        requested_revision = expected_revision
        if requested_revision.tzinfo is None:
            requested_revision = requested_revision.replace(tzinfo=UTC)
        if stored_revision.astimezone(UTC) != requested_revision.astimezone(UTC):
            raise AssetConflictError
    next_title = payload.title.strip() if payload.title is not None else asset.title
    try:
        next_details = (
            normalized_asset_details(asset.type, payload.details)
            if payload.details is not None
            else asset.details
        )
    except ValueError as error:
        raise AssetMetadataError("出版物元数据不完整或格式无效。") from error
    if has_publication_metadata(asset.type, next_details):
        try:
            duplicate = resolve_publication(
                session,
                title=next_title,
                details=next_details,
                exclude_asset_id=asset.id,
            )
        except PublicationIdentityConflictError as error:
            raise AssetMetadataError(str(error)) from error
        if duplicate:
            raise AssetMetadataError("该出版物已经收录，请检查 DOI 或官方来源标识。")
    next_summary = payload.summary.strip() if payload.summary is not None else asset.summary
    next_status = payload.status.strip() if payload.status is not None else asset.status
    next_visibility = payload.visibility if payload.visibility is not None else asset.visibility
    current_tags = sorted(tag.name for tag in asset.tags)
    next_tags = _tag_names(payload.tags) if payload.tags is not None else current_tags
    if (
        next_title == asset.title
        and next_summary == asset.summary
        and next_status == asset.status
        and next_visibility == asset.visibility
        and next_tags == current_tags
        and next_details == asset.details
    ):
        return asset_summary(asset)
    asset.title = next_title
    asset.summary = next_summary
    asset.status = next_status
    asset.visibility = next_visibility
    if next_tags != current_tags:
        asset.tags = _tags(session, next_tags)
    asset.details = next_details
    asset.updated_at = datetime.now(UTC)
    synchronize_publication_identity_keys(asset)
    record_activity(
        session,
        asset=asset,
        actor=actor,
        credential_name=credential_name,
        action=ActivityAction.UPDATED_METADATA,
        description="更新了资产基础信息",
    )
    try:
        session.flush()
    except IntegrityError as error:
        _translate_asset_integrity_error(error)
    return asset_summary(asset)


def asset_for_version_update_statement(asset_id: UUID):
    return (
        select(Asset)
        .where(Asset.id == asset_id, Asset.archived_at.is_(None))
        .options(selectinload(Asset.versions))
        .with_for_update()
    )


def add_asset_version(
    session: Session, asset_id: UUID, payload: AssetVersionCreateRequest, *, actor: User
) -> AssetVersionSummary:
    asset = session.scalar(asset_for_version_update_statement(asset_id))
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
    record_activity(
        session,
        asset=asset,
        actor=actor,
        action=ActivityAction.ADDED_VERSION,
        description=f"登记了版本 {version_name}{current_label}",
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
    record_activity(
        session,
        asset=asset,
        actor=actor,
        action=ActivityAction.ARCHIVED,
        description="归档了该资产",
    )
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
    record_activity(
        session,
        asset=asset,
        actor=actor,
        action=ActivityAction.RESTORED,
        description="恢复了该资产",
    )
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


def list_asset_choices(
    session: Session, *, query: str | None, limit: int
) -> list[AssetChoiceSummary]:
    statement = select(Asset).where(Asset.archived_at.is_(None))
    if query and query.strip():
        pattern = _contains_pattern(" ".join(query.split()))
        statement = statement.where(
            or_(
                Asset.title.ilike(pattern, escape="\\"),
                Asset.slug.ilike(pattern, escape="\\"),
            )
        )
    statement = statement.order_by(Asset.title.asc(), Asset.id.asc()).limit(limit)
    return [AssetChoiceSummary.model_validate(asset) for asset in session.scalars(statement)]


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


def _publication_search_fields():
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


def _publication_metadata_matches(pattern: str):
    return and_(
        Asset.type.in_(PUBLICATION_ASSET_TYPES),
        or_(*(field.ilike(pattern, escape="\\") for field in _publication_search_fields())),
    )


def _term_matches(term: str):
    pattern = _contains_pattern(term)
    return or_(
        Asset.title.ilike(pattern, escape="\\"),
        Asset.summary.ilike(pattern, escape="\\"),
        Asset.tags.any(Tag.name.ilike(pattern, escape="\\")),
        Asset.owner.has(User.name.ilike(pattern, escape="\\")),
        Asset.files.any(FileRecord.file_name.ilike(pattern, escape="\\")),
        _publication_metadata_matches(pattern),
    )


def _search_relevance(query: str, terms: list[str]):
    phrase = " ".join(query.strip().strip('"').split())
    phrase_pattern = _contains_pattern(phrase)
    score = case((func.lower(Asset.title) == phrase.casefold(), 160), else_=0)
    score += case((Asset.title.ilike(_prefix_pattern(phrase), escape="\\"), 100), else_=0)
    score += case((Asset.title.ilike(phrase_pattern, escape="\\"), 80), else_=0)
    score += case((Asset.tags.any(Tag.name.ilike(phrase_pattern, escape="\\")), 65), else_=0)
    score += case((Asset.owner.has(User.name.ilike(phrase_pattern, escape="\\")), 55), else_=0)
    score += case((Asset.summary.ilike(phrase_pattern, escape="\\"), 40), else_=0)
    score += case((_publication_metadata_matches(phrase_pattern), 35), else_=0)
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
        score += case((_publication_metadata_matches(pattern), 8), else_=0)
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
            [
                Asset.type.in_(PUBLICATION_ASSET_TYPES),
                Asset.details["venue"].as_string() == venue.strip(),
            ]
        )
    if year is not None:
        filters.extend(
            [
                Asset.type.in_(PUBLICATION_ASSET_TYPES),
                Asset.details["year"].as_integer() == year,
            ]
        )
    if terms := _search_terms(query):
        filters.append(and_(*(_term_matches(term) for term in terms)))

    return filters


def list_publications_for_citation_export(
    session: Session,
    *,
    asset_type: AssetType,
    query: str | None,
    status: str | None,
    visibility: Visibility | None,
    has_files: bool | None,
    venue: str | None,
    year: int | None,
) -> list[AssetSummary]:
    filters = _asset_filters(
        asset_type=asset_type,
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


def list_publication_catalogue_facets(
    session: Session, asset_type: AssetType
) -> PublicationCatalogueFacets:
    statement = select(Asset.details).where(
        Asset.type == asset_type,
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
    return PublicationCatalogueFacets(venues=venues, years=years)


def list_archived_assets(
    session: Session, *, page: int, page_size: int
) -> tuple[list[AssetSummary], int]:
    filters = [Asset.archived_at.is_not(None)]
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
        .order_by(Asset.archived_at.desc(), Asset.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [asset_summary(asset) for asset in session.scalars(statement).all()], total


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
    try:
        session.flush()
    except IntegrityError as error:
        if violates_constraint(error, ASSET_RELATION_UNIQUE_CONSTRAINT):
            raise AssetRelationError("相同的关联已存在。") from error
        raise
    _record_relation_activities(
        session,
        source=source,
        target=target,
        actor=actor,
        action=ActivityAction.LINKED_ASSET,
        source_description=f"建立了指向资产「{target.title}」的关联：{relation_type}",
        target_description=(
            f"资产「{source.title}」建立了指向本资产的关联：{relation_type}"
        ),
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
    source = session.get(Asset, relation.source_asset_id)
    target = session.get(Asset, relation.target_asset_id)
    if not source or not target:
        raise AssetRelationError("关联资产不存在，无法移除关联。")
    session.delete(relation)
    _record_relation_activities(
        session,
        source=source,
        target=target,
        actor=actor,
        action=ActivityAction.UNLINKED_ASSET,
        source_description=(
            f"解除了指向资产「{target.title}」的关联：{relation.relation_type}"
        ),
        target_description=(
            f"资产「{source.title}」解除了指向本资产的关联：{relation.relation_type}"
        ),
    )


def _record_relation_activities(
    session: Session,
    *,
    source: Asset,
    target: Asset,
    actor: User,
    action: ActivityAction,
    source_description: str,
    target_description: str,
) -> None:
    operation_id = uuid4()
    created_at = datetime.now(UTC)
    record_activity(
        session,
        asset=source,
        actor=actor,
        action=action,
        description=source_description,
        operation_id=operation_id,
        operation_role=ActivityOperationRole.SOURCE,
        created_at=created_at,
    )
    record_activity(
        session,
        asset=target,
        actor=actor,
        action=action,
        description=target_description,
        operation_id=operation_id,
        operation_role=ActivityOperationRole.TARGET,
        created_at=created_at,
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
        recent_activities=recent_activity_summaries(session, limit=20, asset_id=asset.id),
    )
