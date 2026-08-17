from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.domain.enums import AssetType, Visibility
from app.domain.models import (
    Activity,
    Asset,
    AssetRelation,
    AssetVersion,
    FileRecord,
    PublicationIdentityKey,
    Tag,
    User,
)
from app.domain.schemas import (
    AssetCreateRequest,
    AssetRelationCreateRequest,
    AssetUpdateRequest,
    AssetVersionCreateRequest,
    BatchAssetImportRequest,
)
from app.services.assets import (
    AssetMetadataError,
    AssetRelationError,
    AssetSlugConflictError,
    AssetVersionError,
    BatchAssetImportError,
    add_asset_relation,
    add_asset_version,
    archive_asset,
    asset_for_archive_transition_statement,
    asset_for_version_update_statement,
    asset_relation_for_removal_statement,
    assets_for_relation_statement,
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
    tag_insert_statement,
    update_asset,
)
from app.services.citations import (
    PublicationCitationError,
    build_publication_citation,
    build_publication_citation_export,
)
from app.services.paper_identity import matching_publications


def make_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def payload() -> AssetCreateRequest:
    return AssetCreateRequest(
        type=AssetType.DATASET,
        slug="soil-samples-2026",
        title="土壤样本观测数据集",
        summary="田野采样的模拟登记数据。",
        status="draft",
        visibility=Visibility.PROJECT,
        version="v0.1",
        tags=["生态", "田野", "生态"],
        details={"format": "CSV"},
        owner_name="王雪",
        owner_email="wangxue@sage.lab",
    )


def paper_payload(
    *, slug: str = "acl-2026-octotools", asset_type: AssetType = AssetType.PAPER
) -> AssetCreateRequest:
    return AssetCreateRequest(
        type=asset_type,
        slug=slug,
        title="OctoTools: A Multi-Agent Framework with Extensible Tools for Complex Reasoning",
        summary="Multi-agent reasoning framework.",
        status="published",
        details={
            "venue": "ACL",
            "year": 2026,
            "track": "Main Conference - Long Papers",
            "authors": ["Pan Lu", "Bowen Chen"],
            "source_id": "2026.acl-long.1",
            "source_url": "https://aclanthology.org/2026.acl-long.1/",
            "publication_url": "https://aclanthology.org/2026.acl-long.1/",
            "pdf_url": "https://aclanthology.org/2026.acl-long.1.pdf",
            "doi": "https://doi.org/10.18653/v1/2026.acl-long.1",
        },
    )


def test_publication_summary_populates_missing_abstract() -> None:
    request = paper_payload(asset_type=AssetType.LITERATURE)

    assert request.details["abstract"] == request.summary


def test_publication_identity_lookup_uses_exists_for_postgres_json_assets() -> None:
    class CapturingSession:
        def __init__(self) -> None:
            self.statements = []

        def scalars(self, statement):
            self.statements.append(statement)
            return []

    session = CapturingSession()
    assert (
        matching_publications(
            session,
            title="A Reliable Publication Identity",
            details={"authors": ["Sage Researcher"], "source_id": "arxiv:2608.00001"},
        )
        == []
    )

    compiled = str(session.statements[0].compile(dialect=postgresql.dialect())).upper()
    assert "EXISTS" in compiled
    assert "DISTINCT" not in compiled


def test_create_asset_creates_owner_tags_version_and_activity() -> None:
    session = make_session()

    result = create_asset(session, payload())
    session.commit()

    asset = session.scalar(select(Asset).where(Asset.id == result.id))
    assert asset is not None
    assert asset.slug == "soil-samples-2026"
    assert asset.owner.name == "王雪"
    assert [tag.name for tag in session.scalars(select(Tag).order_by(Tag.name)).all()] == [
        "生态",
        "田野",
    ]
    assert session.scalar(select(AssetVersion.version)) == "v0.1"
    assert session.scalar(select(Activity.action)) == "created"
    assert session.scalar(select(User.email)) == "wangxue@sage.lab"
    assert result.file_count == 0
    assert result.default_upload_directory == "raw"
    assert [directory.name for directory in result.upload_directories] == [
        "raw",
        "processed",
        "documentation",
        "scripts",
    ]


def test_tag_inserts_ignore_concurrent_name_conflicts() -> None:
    for dialect_name, dialect in (
        ("postgresql", postgresql.dialect()),
        ("sqlite", sqlite.dialect()),
    ):
        compiled = str(
            tag_insert_statement(dialect_name, ["shared-tag"]).compile(dialect=dialect)
        )
        assert "ON CONFLICT (name) DO NOTHING" in compiled

    session = make_session()
    first = create_asset(session, payload())
    second_payload = payload().model_copy(
        update={"slug": "second-tagged-asset", "title": "Second tagged asset"}
    )
    second = create_asset(session, second_payload)
    session.commit()

    assert session.scalar(select(func.count()).select_from(Tag)) == 2
    assert session.get(Asset, first.id).tags == session.get(Asset, second.id).tags


def test_create_asset_rejects_duplicate_slug() -> None:
    session = make_session()
    create_asset(session, payload())
    session.commit()

    with pytest.raises(AssetSlugConflictError):
        create_asset(session, payload())


def test_database_slug_conflict_is_translated_after_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    created = create_asset(session, payload())
    session.commit()
    actor = session.get(User, created.owner.id)
    assert actor is not None
    original_scalar = session.scalar
    monkeypatch.setattr(session, "scalar", lambda statement: None)

    with pytest.raises(AssetSlugConflictError):
        create_asset(session, payload(), actor=actor)

    monkeypatch.setattr(session, "scalar", original_scalar)
    session.rollback()


def test_paper_metadata_is_normalized_and_duplicate_sources_are_rejected() -> None:
    session = make_session()
    created = create_asset(session, paper_payload())
    session.commit()

    assert created.details["venue"] == "ACL"
    assert created.details["doi"] == "10.18653/v1/2026.acl-long.1"
    assert created.details["publication_url"] == "https://aclanthology.org/2026.acl-long.1/"
    assert session.scalar(select(func.count()).select_from(PublicationIdentityKey)) == 3
    with pytest.raises(AssetMetadataError):
        create_asset(
            session,
            paper_payload(slug="same-paper-second-slug", asset_type=AssetType.LITERATURE),
        )


def test_database_publication_conflict_is_translated_after_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    created = create_asset(session, paper_payload())
    session.commit()
    actor = session.get(User, created.owner.id)
    assert actor is not None
    monkeypatch.setattr("app.services.assets.resolve_publication", lambda *args, **kwargs: None)

    with pytest.raises(AssetMetadataError, match="已经收录"):
        create_asset(
            session,
            paper_payload(slug="concurrent-publication", asset_type=AssetType.LITERATURE),
            actor=actor,
        )

    session.rollback()


def test_paper_bibtex_uses_structured_metadata_and_stable_fallbacks() -> None:
    session = make_session()
    paper = create_asset(session, paper_payload())

    citation = build_publication_citation(paper)

    assert citation.citation_key == "acl-2026-octotools"
    assert citation.filename == "acl-2026-octotools.bib"
    assert citation.bibtex == (
        "@inproceedings{acl-2026-octotools,\n"
        "  title = {OctoTools: A Multi-Agent Framework with Extensible Tools "
        "for Complex Reasoning},\n"
        "  author = {Pan Lu and Bowen Chen},\n"
        "  booktitle = {Proceedings of ACL 2026},\n"
        "  year = {2026},\n"
        "  doi = {10.18653/v1/2026.acl-long.1},\n"
        "  url = {https://aclanthology.org/2026.acl-long.1/}\n"
        "}\n"
    )


def test_paper_bibtex_export_joins_records_once() -> None:
    session = make_session()
    first = create_asset(session, paper_payload())
    second_payload = paper_payload(slug="iclr-2025-paper").model_copy(deep=True)
    second_payload.title = "A Distinct ICLR Paper"
    second_payload.details.update(
        venue="ICLR",
        year=2025,
        source_id="iclr-2025-paper",
        source_url="https://openreview.net/forum?id=iclr-2025-paper",
        publication_url="https://openreview.net/forum?id=iclr-2025-paper",
        pdf_url="https://openreview.net/pdf?id=iclr-2025-paper",
        doi="",
        citation_key="smith2025distinct",
        booktitle="International Conference on Learning Representations",
    )
    second = create_asset(session, second_payload)

    export = build_publication_citation_export([first, second], filename="sage-papers.bib")

    assert export.count == 2
    assert export.filename == "sage-papers.bib"
    assert export.bibtex.count("@inproceedings{") == 2
    assert "@inproceedings{smith2025distinct," in export.bibtex


def test_publication_citation_export_does_not_load_unneeded_relationships() -> None:
    session = make_session()
    create_asset(session, paper_payload())
    session.commit()
    session.expire_all()

    publications = list_publications_for_citation_export(
        session,
        asset_type=AssetType.PAPER,
        query=None,
        status=None,
        visibility=None,
        has_files=None,
        venue=None,
        year=None,
    )

    assert len(publications) == 1
    assert {"owner", "tags", "versions", "files"} <= inspect(publications[0]).unloaded
    export = build_publication_citation_export(publications)
    assert export.count == 1
    assert "OctoTools" in export.bibtex


def test_paper_bibtex_export_rejects_duplicate_citation_keys() -> None:
    session = make_session()
    first = create_asset(session, paper_payload())
    second_payload = paper_payload(slug="second-paper").model_copy(deep=True)
    second_payload.title = "A Different Paper"
    second_payload.details.update(
        source_id="second-paper",
        source_url="https://example.com/second-paper",
        publication_url="https://example.com/second-paper",
        pdf_url="https://example.com/second-paper.pdf",
        doi="10.1000/second-paper",
        citation_key="acl-2026-octotools",
    )
    second = create_asset(session, second_payload)

    with pytest.raises(PublicationCitationError, match="引用键重复"):
        build_publication_citation_export([first, second])


def test_batch_import_rejects_papers_sharing_any_canonical_identity() -> None:
    session = make_session()
    actor = User(name="管理员", email="paper-admin@sage.lab")
    session.add(actor)
    session.flush()
    first = paper_payload(slug="paper-by-source")
    same_doi = paper_payload(slug="paper-by-doi").model_copy(deep=True)
    same_doi.details["source_id"] = "different-source-id"

    with pytest.raises(BatchAssetImportError):
        import_assets(session, BatchAssetImportRequest(assets=[first, same_doi]), actor=actor)


def test_paper_list_filters_by_venue_and_year() -> None:
    session = make_session()
    paper = create_asset(session, paper_payload())
    create_asset(session, payload())
    session.commit()

    matching, total = list_assets(
        session,
        asset_type=AssetType.PAPER,
        query=None,
        status=None,
        visibility=None,
        has_files=None,
        venue="ACL",
        year=2026,
        page=1,
        page_size=20,
    )

    assert total == 1
    assert [item.id for item in matching] == [paper.id]

def test_asset_list_aggregates_file_stats_without_loading_file_records() -> None:
    session = make_session()
    created = create_asset(session, payload())
    session.add_all(
        [
            FileRecord(
                asset_id=created.id,
                relative_path="dataset/soil-samples-2026/raw/first.csv",
                file_name="first.csv",
                file_kind="data",
                file_size=128,
            ),
            FileRecord(
                asset_id=created.id,
                relative_path="dataset/soil-samples-2026/raw/second.csv",
                file_name="second.csv",
                file_kind="data",
                file_size=256,
            ),
        ]
    )
    session.commit()
    session.expunge_all()
    asset = session.get(Asset, created.id)
    assert asset is not None
    assert "files" in inspect(asset).unloaded

    matching, total = list_assets(
        session,
        asset_type=AssetType.DATASET,
        query=None,
        status=None,
        visibility=None,
        has_files=None,
        venue=None,
        year=None,
        page=1,
        page_size=20,
    )

    assert total == 1
    assert [(item.file_count, item.total_size) for item in matching] == [(2, 384)]
    assert "files" in inspect(asset).unloaded



def test_search_requires_every_term_and_prioritizes_title_matches() -> None:
    session = make_session()
    title_match = create_asset(session, paper_payload(slug="title-match"))
    metadata_match_payload = paper_payload(slug="metadata-match").model_copy(deep=True)
    metadata_match_payload.title = "A Study of Tool Use"
    metadata_match_payload.summary = "A framework for complex workflows."
    metadata_match_payload.details["authors"] = ["OctoTools Group", "Bowen Chen"]
    metadata_match_payload.details["source_id"] = "2026.acl-long.2"
    metadata_match_payload.details["doi"] = "https://doi.org/10.18653/v1/2026.acl-long.2"
    metadata_match = create_asset(session, metadata_match_payload)
    unrelated_payload = payload().model_copy(
        update={"slug": "unrelated", "title": "Complex soil observations"}
    )
    create_asset(session, unrelated_payload)
    session.commit()

    matching, total = list_assets(
        session,
        asset_type=None,
        query="OctoTools complex",
        status=None,
        visibility=None,
        has_files=None,
        venue=None,
        year=None,
        page=1,
        page_size=20,
    )

    assert total == 2
    assert [item.id for item in matching] == [title_match.id, metadata_match.id]


def test_search_matches_paper_metadata_owner_and_file_name() -> None:
    session = make_session()
    paper = create_asset(session, paper_payload())
    session.add(
        FileRecord(
            asset_id=paper.id,
            relative_path="paper/acl-2026-octotools/artifacts/evaluation-results.csv",
            file_name="evaluation-results.csv",
            file_kind="data",
            file_size=128,
        )
    )
    session.commit()

    for query in ["Pan Lu", "10.18653/v1/2026.acl-long.1", "evaluation-results"]:
        matching, total = list_assets(
            session,
            asset_type=None,
            query=query,
            status=None,
            visibility=None,
            has_files=None,
            venue=None,
            year=None,
            page=1,
            page_size=20,
        )

        assert total == 1
        assert [item.id for item in matching] == [paper.id]


def test_search_treats_sql_wildcards_as_literal_characters() -> None:
    session = make_session()
    create_asset(session, payload())
    session.commit()

    matching, total = list_assets(
        session,
        asset_type=None,
        query="%",
        status=None,
        visibility=None,
        has_files=None,
        venue=None,
        year=None,
        page=1,
        page_size=20,
    )

    assert total == 0
    assert matching == []


def test_search_matches_metadata_values_without_matching_json_field_names() -> None:
    session = make_session()
    paper = create_asset(session, paper_payload())
    session.commit()

    value_matches, value_total = list_assets(
        session,
        asset_type=None,
        query="ACL",
        status=None,
        visibility=None,
        has_files=None,
        venue=None,
        year=None,
        page=1,
        page_size=20,
    )
    key_matches, key_total = list_assets(
        session,
        asset_type=None,
        query="venue",
        status=None,
        visibility=None,
        has_files=None,
        venue=None,
        year=None,
        page=1,
        page_size=20,
    )

    assert value_total == 1
    assert [item.id for item in value_matches] == [paper.id]
    assert key_total == 0
    assert key_matches == []


def test_asset_pagination_has_a_stable_unique_order() -> None:
    session = make_session()
    assets = []
    for index in range(5):
        item = payload().model_copy(
            update={"slug": f"stable-order-{index}", "title": "Shared title"}
        )
        assets.append(create_asset(session, item))
    shared_time = session.get(Asset, assets[0].id).updated_at
    for item in assets:
        session.get(Asset, item.id).updated_at = shared_time
    session.commit()

    def pages() -> list[str]:
        return [
            str(item.id)
            for page in range(1, 4)
            for item in list_assets(
                session,
                asset_type=AssetType.DATASET,
                query="Shared",
                status=None,
                visibility=None,
                has_files=None,
                venue=None,
                year=None,
                page=page,
                page_size=2,
            )[0]
        ]

    first_read = pages()
    second_read = pages()

    assert first_read == second_read
    assert len(first_read) == len(set(first_read)) == 5


def test_asset_choices_search_beyond_catalogue_page_and_exclude_archived() -> None:
    session = make_session()
    created = []
    for index in range(105):
        item = payload().model_copy(
            update={
                "slug": f"candidate-{index:03d}",
                "title": f"候选资产 {index:03d}",
            }
        )
        created.append(create_asset(session, item))
    archived = session.get(Asset, created[-1].id)
    archived.archived_at = archived.updated_at
    session.commit()

    matches = list_asset_choices(session, query="candidate-103", limit=20)
    archived_matches = list_asset_choices(session, query="candidate-104", limit=20)

    assert len(matches) == 1
    assert matches[0].slug == "candidate-103"
    assert matches[0].model_dump().keys() == {"id", "type", "slug", "title"}
    assert archived_matches == []


def test_publication_catalogue_facets_are_isolated_by_catalogue() -> None:
    session = make_session()
    acl = paper_payload()
    duplicate_acl = paper_payload(slug="acl-2026-second-paper").model_copy(deep=True)
    duplicate_acl.title = "A Second ACL Paper"
    duplicate_acl.status = "accepted"
    duplicate_acl.details.update(
        source_id="acl-2026-second-paper",
        source_url="https://example.com/acl-2026-second-paper",
        publication_url="https://example.com/acl-2026-second-paper",
        pdf_url="https://example.com/acl-2026-second-paper.pdf",
        doi="10.1000/acl-2026-second-paper",
    )
    iclr = paper_payload(slug="iclr-2025-paper", asset_type=AssetType.LITERATURE).model_copy(
        deep=True
    )
    iclr.title = "A Distinct ICLR Paper"
    iclr.details.update(
        venue="ICLR",
        year=2025,
        source_id="iclr-2025-paper",
        source_url="https://openreview.net/forum?id=iclr-2025-paper",
        publication_url="https://openreview.net/forum?id=iclr-2025-paper",
        pdf_url="https://openreview.net/pdf?id=iclr-2025-paper",
        doi="",
    )
    archived = create_asset(session, iclr)
    create_asset(session, acl)
    create_asset(session, duplicate_acl)
    session.commit()

    facets = list_publication_catalogue_facets(session, AssetType.PAPER)
    literature_facets = list_publication_catalogue_facets(session, AssetType.LITERATURE)
    assert facets.venues == ["ACL"]
    assert facets.years == [2026]
    assert facets.statuses == ["accepted", "published"]
    assert literature_facets.venues == ["ICLR"]
    assert literature_facets.years == [2025]
    assert literature_facets.statuses == ["published"]

    asset = session.get(Asset, archived.id)
    assert asset is not None
    asset.archived_at = asset.updated_at
    session.commit()

    active_facets = list_publication_catalogue_facets(session, AssetType.LITERATURE)
    assert active_facets.venues == []
    assert active_facets.years == []
    assert active_facets.statuses == []


def test_asset_metadata_can_be_updated_archived_and_restored() -> None:
    session = make_session()
    result = create_asset(session, payload())
    actor = session.get(User, result.owner.id)
    assert actor is not None

    updated = update_asset(
        session,
        result.id,
        AssetUpdateRequest(title="更新后的土壤样本", tags=["新标签"], status="active"),
        actor=actor,
    )
    archived = archive_asset(session, result.id, actor=actor)

    assert updated.title == "更新后的土壤样本"
    assert updated.tags == ["新标签"]
    archived_items, archived_total = list_archived_assets(session, page=1, page_size=20)
    assert archived_items[0].id == archived.id
    assert archived_total == 1

    restored = restore_asset(session, result.id, actor=actor)

    assert restored.id == result.id
    assert (
        session.scalar(select(Activity.action).order_by(Activity.created_at.desc())) == "restored"
    )


def test_archive_transitions_lock_the_asset_row() -> None:
    asset_id = uuid4()
    active_statement = asset_for_archive_transition_statement(asset_id, archived=False)
    archived_statement = asset_for_archive_transition_statement(asset_id, archived=True)

    active_sql = str(active_statement.compile(dialect=postgresql.dialect()))
    archived_sql = str(archived_statement.compile(dialect=postgresql.dialect()))

    assert "assets.archived_at IS NULL" in active_sql
    assert "assets.archived_at IS NOT NULL" in archived_sql
    assert active_sql.endswith("FOR UPDATE")
    assert archived_sql.endswith("FOR UPDATE")


def test_archived_assets_are_paginated_in_stable_archive_order() -> None:
    session = make_session()
    actor = User(username="archivist", name="Archiver", email="archivist@sage.lab")
    session.add(actor)
    session.flush()
    archived_assets: list[Asset] = []
    for index in range(23):
        asset = Asset(
            type=AssetType.DATASET,
            slug=f"archive-{index:02d}",
            title=f"Archive {index:02d}",
            summary="Archived test asset",
            status="archived",
            visibility=Visibility.LAB,
            owner=actor,
            details={},
            archived_at=datetime(2026, 8, 14, 3, index, tzinfo=UTC),
        )
        archived_assets.append(asset)
    active = Asset(
        type=AssetType.DATASET,
        slug="active-dataset",
        title="Active dataset",
        summary="Not archived",
        status="active",
        visibility=Visibility.LAB,
        owner=actor,
        details={},
    )
    session.add_all([*archived_assets, active])
    session.commit()

    first_page, total = list_archived_assets(session, page=1, page_size=20)
    second_page, second_total = list_archived_assets(session, page=2, page_size=20)

    assert total == 23
    assert second_total == 23
    assert len(first_page) == 20
    assert [item.slug for item in second_page] == ["archive-02", "archive-01", "archive-00"]


def test_asset_metadata_update_ignores_semantically_equivalent_replays() -> None:
    session = make_session()
    result = create_asset(session, payload())
    session.commit()
    actor = session.get(User, result.owner.id)
    asset = session.get(Asset, result.id)
    assert actor is not None
    assert asset is not None
    original_updated_at = asset.updated_at
    original_activity_count = session.scalar(select(func.count()).select_from(Activity))

    replayed = update_asset(
        session,
        result.id,
        AssetUpdateRequest(
            title=f"  {asset.title}  ",
            summary=f"  {asset.summary}  ",
            status=f"  {asset.status}  ",
            visibility=asset.visibility,
            tags=["田野", "生态", "田野"],
            details=dict(asset.details),
        ),
        actor=actor,
    )
    session.commit()

    assert replayed.title == asset.title
    assert session.get(Asset, result.id).updated_at == original_updated_at
    assert session.scalar(select(func.count()).select_from(Activity)) == original_activity_count


def test_tag_only_asset_update_advances_updated_at() -> None:
    session = make_session()
    result = create_asset(session, payload())
    actor = session.get(User, result.owner.id)
    asset = session.get(Asset, result.id)
    assert actor is not None
    assert asset is not None
    previous_updated_at = datetime(2025, 1, 1, tzinfo=UTC)
    asset.updated_at = previous_updated_at
    session.commit()
    persisted_previous_updated_at = session.get(Asset, result.id).updated_at

    updated = update_asset(
        session,
        result.id,
        AssetUpdateRequest(tags=["only-new-tag"]),
        actor=actor,
    )
    session.commit()

    assert updated.tags == ["only-new-tag"]
    assert session.get(Asset, result.id).updated_at > persisted_previous_updated_at
    assert (
        session.scalar(
            select(func.count()).select_from(Activity).where(Activity.action == "updated_metadata")
        )
        == 1
    )


def test_asset_list_filters_status_visibility_and_file_presence() -> None:
    session = make_session()
    first = create_asset(session, payload())
    second = create_asset(
        session,
        payload().model_copy(
            update={
                "slug": "published-soil-samples",
                "title": "已发布样本",
                "status": "available",
                "visibility": Visibility.LAB,
            }
        ),
    )
    session.add(
        FileRecord(
            asset_id=second.id,
            relative_path="dataset/published-soil-samples/raw/samples.csv",
            file_name="samples.csv",
            file_kind="csv",
            file_size=128,
        )
    )
    session.commit()

    matching, total = list_assets(
        session,
        asset_type=AssetType.DATASET,
        query=None,
        status="available",
        visibility=Visibility.LAB,
        has_files=True,
        venue=None,
        year=None,
        page=1,
        page_size=20,
    )
    no_files, no_files_total = list_assets(
        session,
        asset_type=AssetType.DATASET,
        query=None,
        status=None,
        visibility=None,
        has_files=False,
        venue=None,
        year=None,
        page=1,
        page_size=20,
    )

    assert total == 1
    assert [item.id for item in matching] == [second.id]
    assert no_files_total == 1
    assert [item.id for item in no_files] == [first.id]

    detail = get_asset(session, second.id)
    assert detail is not None
    assert detail.files[0].relative_path == "dataset/published-soil-samples/raw/samples.csv"


def test_asset_relation_creation_locks_assets_in_stable_order() -> None:
    statement = assets_for_relation_statement(uuid4(), uuid4())

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "assets.archived_at IS NULL" in compiled
    assert "ORDER BY assets.id" in compiled
    assert compiled.endswith("FOR UPDATE")


def test_asset_relation_removal_locks_the_relation_row() -> None:
    statement = asset_relation_for_removal_statement(uuid4(), uuid4())

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "asset_relations.id =" in compiled
    assert "asset_relations.source_asset_id =" in compiled
    assert "asset_relations.target_asset_id =" in compiled
    assert compiled.endswith("FOR UPDATE")


def test_asset_relation_can_be_created_and_removed() -> None:
    session = make_session()
    source = create_asset(session, payload())
    target_payload = payload().model_copy(
        update={"slug": "soil-analysis-notebook", "title": "土壤分析笔记"}
    )
    target = create_asset(session, target_payload)
    actor = session.get(User, source.owner.id)
    assert actor is not None

    relation = add_asset_relation(
        session,
        source.id,
        AssetRelationCreateRequest(target_asset_id=target.id, relation_type="documents"),
        actor=actor,
    )

    detail = get_asset(session, source.id)
    assert relation.id == target.id
    assert detail is not None
    assert detail.related_assets[0].relation_id == relation.relation_id
    assert detail.related_assets[0].title == "土壤分析笔记"
    linked = session.scalars(select(Activity).where(Activity.action == "linked_asset")).all()
    assert {activity.asset_id for activity in linked} == {source.id, target.id}
    assert len({activity.operation_id for activity in linked}) == 1
    assert linked[0].operation_id is not None
    assert {activity.operation_role for activity in linked} == {"source", "target"}
    assert len({activity.created_at for activity in linked}) == 1
    assert {activity.asset_id: activity.description for activity in linked} == {
        source.id: "建立了指向资产「土壤分析笔记」的关联：documents",
        target.id: "资产「土壤样本观测数据集」建立了指向本资产的关联：documents",
    }

    remove_asset_relation(session, target.id, relation.relation_id, actor=actor)

    refreshed = get_asset(session, source.id)
    assert refreshed is not None
    assert refreshed.related_assets == []
    unlinked = session.scalars(select(Activity).where(Activity.action == "unlinked_asset")).all()
    assert {activity.asset_id for activity in unlinked} == {source.id, target.id}
    assert len({activity.operation_id for activity in unlinked}) == 1
    assert unlinked[0].operation_id is not None
    assert {activity.operation_role for activity in unlinked} == {"source", "target"}
    assert len({activity.created_at for activity in unlinked}) == 1
    assert {activity.asset_id: activity.description for activity in unlinked} == {
        source.id: "解除了指向资产「土壤分析笔记」的关联：documents",
        target.id: "资产「土壤样本观测数据集」解除了指向本资产的关联：documents",
    }
    for asset in (source, target):
        detail = get_asset(session, asset.id)
        assert detail is not None
        actions = [activity.action for activity in detail.recent_activities]
        assert actions[:2] == ["unlinked_asset", "linked_asset"]


def test_asset_relation_can_be_removed_when_an_endpoint_is_archived() -> None:
    session = make_session()
    source = create_asset(session, payload())
    target = create_asset(
        session,
        payload().model_copy(update={"slug": "archived-relation-target", "title": "归档关系目标"}),
    )
    actor = session.get(User, source.owner.id)
    assert actor is not None
    relation = add_asset_relation(
        session,
        source.id,
        AssetRelationCreateRequest(target_asset_id=target.id, relation_type="documents"),
        actor=actor,
    )
    target_model = session.get(Asset, target.id)
    assert target_model is not None
    target_model.archived_at = datetime.now(UTC)
    session.flush()

    remove_asset_relation(session, source.id, relation.relation_id, actor=actor)
    session.flush()

    assert session.get(AssetRelation, relation.relation_id) is None
    unlinked = session.scalars(select(Activity).where(Activity.action == "unlinked_asset")).all()
    assert {activity.asset_id for activity in unlinked} == {source.id, target.id}


def test_asset_relation_identity_is_unique_but_direction_and_type_are_distinct() -> None:
    session = make_session()
    source = create_asset(session, payload())
    target = create_asset(
        session,
        payload().model_copy(update={"slug": "relation-target", "title": "关系目标资产"}),
    )
    session.add_all(
        [
            AssetRelation(
                source_asset_id=source.id,
                target_asset_id=target.id,
                relation_type="documents",
            ),
            AssetRelation(
                source_asset_id=target.id,
                target_asset_id=source.id,
                relation_type="documents",
            ),
            AssetRelation(
                source_asset_id=source.id,
                target_asset_id=target.id,
                relation_type="derived-from",
            ),
        ]
    )
    session.flush()
    session.add(
        AssetRelation(
            source_asset_id=source.id,
            target_asset_id=target.id,
            relation_type="documents",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_asset_relation_constraint_conflict_becomes_a_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    source = create_asset(session, payload())
    target = create_asset(
        session,
        payload().model_copy(update={"slug": "relation-conflict-target", "title": "关系冲突目标"}),
    )
    actor = session.get(User, source.owner.id)
    assert actor is not None
    session.commit()

    class Diagnostic:
        constraint_name = "uq_asset_relations_identity"

    class DatabaseError(Exception):
        diag = Diagnostic()

    original_flush = session.flush

    def flush_with_relation_conflict(*args, **kwargs) -> None:
        if any(isinstance(item, AssetRelation) for item in session.new):
            raise IntegrityError("insert", {}, DatabaseError())
        original_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", flush_with_relation_conflict)

    with pytest.raises(AssetRelationError, match="相同的关联已存在"):
        add_asset_relation(
            session,
            source.id,
            AssetRelationCreateRequest(
                target_asset_id=target.id,
                relation_type="documents",
            ),
            actor=actor,
        )

    assert not any(isinstance(item, Activity) for item in session.new)
    session.rollback()
    assert session.scalars(select(Activity).where(Activity.action == "linked_asset")).all() == []


def test_asset_version_can_be_added_and_marked_current() -> None:
    session = make_session()
    result = create_asset(session, payload())
    actor = session.get(User, result.owner.id)
    assert actor is not None

    version = add_asset_version(
        session,
        result.id,
        AssetVersionCreateRequest(version="v0.2", release_notes="补充清洗说明", make_current=True),
        actor=actor,
    )

    versions = session.scalars(select(AssetVersion).order_by(AssetVersion.version)).all()
    assert version.version == "v0.2"
    assert [(item.version, item.is_current) for item in versions] == [
        ("v0.1", False),
        ("v0.2", True),
    ]
    assert (
        session.scalar(select(Activity.action).order_by(Activity.created_at.desc()))
        == "added_version"
    )


def test_version_updates_lock_the_asset_aggregate_and_preserve_one_current() -> None:
    session = make_session()
    result = create_asset(session, payload())
    actor = session.get(User, result.owner.id)
    assert actor is not None

    statement = asset_for_version_update_statement(result.id)
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert compiled.endswith("FOR UPDATE")

    add_asset_version(
        session,
        result.id,
        AssetVersionCreateRequest(version="v0.2", make_current=True),
        actor=actor,
    )
    add_asset_version(
        session,
        result.id,
        AssetVersionCreateRequest(version="v0.3", make_current=True),
        actor=actor,
    )
    add_asset_version(
        session,
        result.id,
        AssetVersionCreateRequest(version="experiment", make_current=False),
        actor=actor,
    )

    versions = session.scalars(select(AssetVersion).order_by(AssetVersion.version)).all()
    assert [(item.version, item.is_current) for item in versions] == [
        ("experiment", False),
        ("v0.1", False),
        ("v0.2", False),
        ("v0.3", True),
    ]
    with pytest.raises(AssetVersionError, match="已经登记"):
        add_asset_version(
            session,
            result.id,
            AssetVersionCreateRequest(version="v0.3", make_current=True),
            actor=actor,
        )
    assert (
        session.scalar(
            select(func.count()).select_from(Activity).where(Activity.action == "added_version")
        )
        == 3
    )


def test_batch_import_is_prevalidated_before_creating_assets() -> None:
    session = make_session()
    actor = User(name="管理员", email="admin@sage.lab")
    session.add(actor)
    session.flush()
    first = payload().model_copy(update={"slug": "batch-one"})
    second = payload().model_copy(update={"slug": "batch-two"})

    created = import_assets(session, BatchAssetImportRequest(assets=[first, second]), actor=actor)

    assert [item.slug for item in created] == ["batch-one", "batch-two"]
    with pytest.raises(BatchAssetImportError):
        import_assets(session, BatchAssetImportRequest(assets=[first, first]), actor=actor)
    assert session.scalar(select(Asset).where(Asset.slug == "batch-one")) is not None


def test_asset_requests_normalize_text_and_tags() -> None:
    request = AssetCreateRequest(
        type=AssetType.DATASET,
        slug="normalized-input",
        title="  Normalized title  ",
        summary="  Normalized summary  ",
        status="  draft  ",
        tags=["  field  ", "field", "", "  analysis"],
    )
    update = AssetUpdateRequest(title="  Updated  ", tags=[" one ", "one", " two "])
    version = AssetVersionCreateRequest(version="  v1.0  ", release_notes="  Notes  ")

    assert request.title == "Normalized title"
    assert request.summary == "Normalized summary"
    assert request.status == "draft"
    assert request.tags == ["field", "analysis"]
    assert update.title == "Updated"
    assert update.tags == ["one", "two"]
    assert version.version == "v1.0"
    assert version.release_notes == "Notes"


def test_asset_requests_reject_empty_required_text_and_oversized_tags() -> None:
    with pytest.raises(ValidationError):
        AssetCreateRequest(
            type=AssetType.DATASET,
            slug="oversized-tag",
            title="Dataset",
            tags=["x" * 81],
        )

    with pytest.raises(ValidationError):
        AssetCreateRequest(type=AssetType.DATASET, slug="empty-title", title="   ")

    with pytest.raises(ValidationError):
        AssetUpdateRequest(tags=["x" * 81])
