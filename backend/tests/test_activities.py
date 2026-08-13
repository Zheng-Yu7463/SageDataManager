from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.domain.enums import AssetType, Visibility
from app.domain.models import Activity, Asset, User
from app.services.activities import activity_summary, recent_activity_summaries


def make_session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_recent_activity_summaries_collapse_equivalent_events() -> None:
    session = make_session()
    actor = User(username="researcher", name="Researcher", email="researcher@sage.lab")
    asset = Asset(
        type=AssetType.LITERATURE,
        slug="activity-summary",
        title="Activity Summary",
        status="published",
        visibility=Visibility.LAB,
        owner=actor,
    )
    session.add(asset)
    session.flush()
    started_at = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
    repeated = [
        Activity(
            asset=asset,
            actor=actor,
            action="prepared_upload",
            description="为 literature/activity-summary/original 生成了上传指令",
            created_at=started_at + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    distinct = Activity(
        asset=asset,
        actor=actor,
        action="downloaded_file",
        description="下载了文件 paper.pdf",
        created_at=started_at + timedelta(minutes=4),
    )
    session.add_all([*repeated, distinct])
    session.commit()

    summaries = recent_activity_summaries(session, limit=20, asset_id=asset.id)

    assert [summary.action for summary in summaries] == [
        "downloaded_file",
        "prepared_upload",
    ]
    assert summaries[0].occurrence_count == 1
    assert summaries[1].occurrence_count == 3
    assert summaries[1].id == repeated[-1].id
    assert summaries[1].created_at == repeated[-1].created_at


def test_recent_activity_summaries_do_not_mix_assets() -> None:
    session = make_session()
    actor = User(username="researcher", name="Researcher", email="researcher@sage.lab")
    first = Asset(
        type=AssetType.DATASET,
        slug="first-dataset",
        title="First Dataset",
        status="draft",
        visibility=Visibility.LAB,
        owner=actor,
    )
    second = Asset(
        type=AssetType.DATASET,
        slug="second-dataset",
        title="Second Dataset",
        status="draft",
        visibility=Visibility.LAB,
        owner=actor,
    )
    session.add_all([first, second])
    session.flush()
    for asset in (first, second):
        session.add(
            Activity(
                asset=asset,
                actor=actor,
                action="updated_metadata",
                description="更新了资产元数据",
            )
        )
    session.commit()

    summaries = recent_activity_summaries(session, limit=20, asset_id=first.id)

    assert len(summaries) == 1
    assert summaries[0].asset_id == first.id
    assert summaries[0].occurrence_count == 1


def test_raw_activity_summary_represents_one_audit_record() -> None:
    session = make_session()
    activity = Activity(action="updated_branding", description="更新了品牌设置")
    session.add(activity)
    session.flush()

    summary = activity_summary(activity)

    assert summary.occurrence_count == 1
