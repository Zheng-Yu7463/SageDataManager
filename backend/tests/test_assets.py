import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.domain.enums import AssetType, Visibility
from app.domain.models import Activity, Asset, AssetVersion, FileRecord, Tag, User
from app.domain.schemas import (
    AssetCreateRequest,
    AssetRelationCreateRequest,
    AssetUpdateRequest,
    AssetVersionCreateRequest,
    BatchAssetImportRequest,
)
from app.services.assets import (
    AssetSlugConflictError,
    BatchAssetImportError,
    add_asset_relation,
    add_asset_version,
    archive_asset,
    create_asset,
    get_asset,
    import_assets,
    list_archived_assets,
    list_assets,
    remove_asset_relation,
    restore_asset,
    update_asset,
)


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


def test_create_asset_rejects_duplicate_slug() -> None:
    session = make_session()
    create_asset(session, payload())
    session.commit()

    with pytest.raises(AssetSlugConflictError):
        create_asset(session, payload())


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
    assert list_archived_assets(session)[0].id == archived.id

    restored = restore_asset(session, result.id, actor=actor)

    assert restored.id == result.id
    assert (
        session.scalar(select(Activity.action).order_by(Activity.created_at.desc())) == "restored"
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
        page=1,
        page_size=20,
    )

    assert total == 1
    assert [item.id for item in matching] == [second.id]
    assert no_files_total == 1
    assert [item.id for item in no_files] == [first.id]


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

    remove_asset_relation(session, target.id, relation.relation_id, actor=actor)

    refreshed = get_asset(session, source.id)
    assert refreshed is not None
    assert refreshed.related_assets == []


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


def test_batch_import_is_prevalidated_before_creating_assets() -> None:
    session = make_session()
    actor = User(name="管理员", email="admin@sage.lab")
    session.add(actor)
    session.flush()
    first = payload().model_copy(update={"slug": "batch-one"})
    second = payload().model_copy(update={"slug": "batch-two"})

    created = import_assets(
        session, BatchAssetImportRequest(assets=[first, second]), actor=actor
    )

    assert [item.slug for item in created] == ["batch-one", "batch-two"]
    with pytest.raises(BatchAssetImportError):
        import_assets(
            session, BatchAssetImportRequest(assets=[first, first]), actor=actor
        )
    assert session.scalar(select(Asset).where(Asset.slug == "batch-one")) is not None
