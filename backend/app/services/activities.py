from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.domain.activity import activity_label
from app.domain.models import Activity, Asset, User
from app.domain.schemas import ActivityFacet, ActivitySummary


def activity_summary(activity: Activity) -> ActivitySummary:
    return ActivitySummary(
        id=activity.id,
        asset_id=activity.asset_id,
        asset_title=activity.asset.title if activity.asset else None,
        asset_type=activity.asset.type if activity.asset else None,
        actor_name=activity.actor.name if activity.actor else None,
        credential_name=activity.credential_name,
        action=activity.action,
        action_label=activity_label(activity.action),
        description=activity.description,
        created_at=activity.created_at,
    )


def recent_activity_summaries(
    session: Session,
    *,
    limit: int,
    asset_id: UUID | None = None,
) -> list[ActivitySummary]:
    partition = (
        Activity.asset_id,
        Activity.actor_id,
        Activity.credential_name,
        Activity.action,
        Activity.description,
    )
    ranked = select(
        Activity.id.label("id"),
        Activity.asset_id.label("asset_id"),
        Activity.actor_id.label("actor_id"),
        Activity.credential_name.label("credential_name"),
        Activity.action.label("action"),
        Activity.description.label("description"),
        Activity.created_at.label("created_at"),
        func.count(Activity.id).over(partition_by=partition).label("occurrence_count"),
        func.row_number()
        .over(
            partition_by=partition,
            order_by=(Activity.created_at.desc(), Activity.id.desc()),
        )
        .label("summary_rank"),
    )
    if asset_id is not None:
        ranked = ranked.where(Activity.asset_id == asset_id)
    ranked_activity = ranked.subquery()
    asset = aliased(Asset)
    actor = aliased(User)
    rows = session.execute(
        select(
            ranked_activity,
            asset.title.label("asset_title"),
            asset.type.label("asset_type"),
            actor.name.label("actor_name"),
        )
        .outerjoin(asset, asset.id == ranked_activity.c.asset_id)
        .outerjoin(actor, actor.id == ranked_activity.c.actor_id)
        .where(ranked_activity.c.summary_rank == 1)
        .order_by(ranked_activity.c.created_at.desc(), ranked_activity.c.id.desc())
        .limit(limit)
    ).mappings()
    return [
        ActivitySummary(
            id=row.id,
            asset_id=row.asset_id,
            asset_title=row.asset_title,
            asset_type=row.asset_type,
            actor_name=row.actor_name,
            credential_name=row.credential_name,
            action=row.action,
            action_label=activity_label(row.action),
            description=row.description,
            created_at=row.created_at,
            occurrence_count=row.occurrence_count,
        )
        for row in rows
    ]


def activity_facets(session: Session) -> list[ActivityFacet]:
    rows = session.execute(
        select(Activity.action, func.count(Activity.id))
        .group_by(Activity.action)
        .order_by(Activity.action.asc())
    ).all()
    return [
        ActivityFacet(value=action, label=activity_label(action), count=count)
        for action, count in rows
    ]
