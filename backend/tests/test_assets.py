import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.domain.enums import AssetType, Visibility
from app.domain.models import Activity, Asset, AssetVersion, Tag, User
from app.domain.schemas import AssetCreateRequest
from app.services.assets import AssetSlugConflictError, create_asset


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


def test_create_asset_rejects_duplicate_slug() -> None:
    session = make_session()
    create_asset(session, payload())
    session.commit()

    with pytest.raises(AssetSlugConflictError):
        create_asset(session, payload())
