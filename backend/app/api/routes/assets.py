from typing import Annotated
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.dependencies import AdminDependency, require_admin
from app.db.session import get_session
from app.domain.enums import AssetType, Visibility
from app.domain.schemas import (
    AssetChoiceSummary,
    AssetCreateRequest,
    AssetDetail,
    AssetListResponse,
    AssetRelationCreateRequest,
    AssetSummary,
    AssetUpdateRequest,
    AssetVersionCreateRequest,
    AssetVersionSummary,
    AssetYamlImportRequest,
    BatchAssetImportRequest,
    BatchAssetImportResponse,
    PublicationCitationExportResponse,
    PublicationCitationResponse,
    RelatedAssetSummary,
)
from app.services.assets import (
    AssetMetadataError,
    AssetNotFoundError,
    AssetRelationError,
    AssetSlugConflictError,
    AssetVersionError,
    BatchAssetImportError,
    add_asset_relation,
    add_asset_version,
    archive_asset,
    create_asset,
    get_asset,
    import_assets,
    list_archived_assets,
    list_asset_choices,
    list_assets,
    list_publication_catalogue_facets,
    list_publications_for_citation_export,
    remove_asset_relation,
    restore_asset,
    update_asset,
)
from app.services.citations import (
    PublicationCitationError,
    build_publication_citation,
    build_publication_citation_export,
)

router = APIRouter(
    prefix="/assets",
    tags=["assets"],
    dependencies=[Depends(require_admin)],
)
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/choices")
def asset_choices(
    session: SessionDependency,
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[AssetChoiceSummary]:
    return list_asset_choices(session, query=query, limit=limit)


@router.get("")
def assets(
    session: SessionDependency,
    asset_type: AssetType | None = None,
    asset_status: str | None = Query(default=None, alias="status", max_length=40),
    visibility: Visibility | None = None,
    has_files: bool | None = None,
    venue: str | None = Query(default=None, min_length=2, max_length=80),
    year: int | None = Query(default=None, ge=1900, le=2200),
    query: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> AssetListResponse:
    items, total = list_assets(
        session,
        asset_type=asset_type,
        query=query,
        page=page,
        status=asset_status,
        visibility=visibility,
        has_files=has_files,
        venue=venue,
        year=year,
        page_size=page_size,
    )

    publication_facets = (
        list_publication_catalogue_facets(session, asset_type)
        if asset_type in {AssetType.PAPER, AssetType.LITERATURE}
        else None
    )
    return AssetListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        publication_facets=publication_facets,
    )


@router.get("/citations/bibtex")
def export_bibtex(
    session: SessionDependency,
    _: AdminDependency,
    asset_type: AssetType = AssetType.PAPER,
    asset_status: str | None = Query(default=None, alias="status", max_length=40),
    visibility: Visibility | None = None,
    has_files: bool | None = None,
    venue: str | None = Query(default=None, min_length=2, max_length=80),
    year: int | None = Query(default=None, ge=1900, le=2200),
    query: str | None = Query(default=None, max_length=200),
) -> PublicationCitationExportResponse:
    if asset_type not in {AssetType.PAPER, AssetType.LITERATURE}:
        raise HTTPException(status_code=422, detail="只有论文或文献目录支持 BibTeX 导出。")
    publications = list_publications_for_citation_export(
        session,
        asset_type=asset_type,
        query=query,
        status=asset_status,
        visibility=visibility,
        has_files=has_files,
        venue=venue,
        year=year,
    )
    try:
        filename = "sage-papers.bib" if asset_type == AssetType.PAPER else "sage-literature.bib"
        return build_publication_citation_export(publications, filename=filename)
    except PublicationCitationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


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
    except AssetMetadataError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    except Exception:
        session.rollback()
        raise


@router.post("/import", status_code=status.HTTP_201_CREATED)
def import_metadata(
    payload: BatchAssetImportRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> BatchAssetImportResponse:
    try:
        created = import_assets(session, payload, actor=current_user)
        session.commit()
        return BatchAssetImportResponse(created=created)
    except BatchAssetImportError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    except Exception:
        session.rollback()
        raise


@router.post("/import/yaml", status_code=status.HTTP_201_CREATED)
def import_yaml_metadata(
    payload: AssetYamlImportRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> BatchAssetImportResponse:
    try:
        parsed = yaml.safe_load(payload.content)
        assets = (
            parsed
            if isinstance(parsed, list)
            else parsed.get("assets")
            if isinstance(parsed, dict)
            else None
        )
        request = BatchAssetImportRequest.model_validate({"assets": assets})
        created = import_assets(session, request, actor=current_user)
        session.commit()
        return BatchAssetImportResponse(created=created)
    except yaml.YAMLError:
        session.rollback()
        raise HTTPException(status_code=422, detail="YAML 格式无效。") from None
    except ValidationError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=error.errors()) from None
    except BatchAssetImportError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
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
    except AssetMetadataError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=error.message) from None
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


@router.post("/{asset_id}/versions", status_code=status.HTTP_201_CREATED)
def add_version(
    asset_id: UUID,
    payload: AssetVersionCreateRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> AssetVersionSummary:
    try:
        version = add_asset_version(session, asset_id, payload, actor=current_user)
        session.commit()
        return version
    except AssetNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="资产不存在或已归档。") from None
    except AssetVersionError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    except Exception:
        session.rollback()
        raise


@router.post("/{asset_id}/relations", status_code=status.HTTP_201_CREATED)
def add_relation(
    asset_id: UUID,
    payload: AssetRelationCreateRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> RelatedAssetSummary:
    try:
        relation = add_asset_relation(session, asset_id, payload, actor=current_user)
        session.commit()
        return relation
    except AssetRelationError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=error.message) from None
    except Exception:
        session.rollback()
        raise


@router.delete("/{asset_id}/relations/{relation_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_relation(
    asset_id: UUID,
    relation_id: UUID,
    session: SessionDependency,
    current_user: AdminDependency,
) -> None:
    try:
        remove_asset_relation(session, asset_id, relation_id, actor=current_user)
        session.commit()
    except AssetRelationError as error:
        session.rollback()
        raise HTTPException(status_code=404, detail=error.message) from None
    except Exception:
        session.rollback()
        raise


@router.get("/{asset_id}")
def asset(asset_id: UUID, session: SessionDependency) -> AssetDetail:
    result = get_asset(session, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="Asset not found")
    return result


@router.get("/{asset_id}/citation/bibtex")
def publication_bibtex(
    asset_id: UUID, session: SessionDependency, _: AdminDependency
) -> PublicationCitationResponse:
    result = get_asset(session, asset_id)
    if not result:
        raise HTTPException(status_code=404, detail="资产不存在或已归档。")
    try:
        return build_publication_citation(result)
    except PublicationCitationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
