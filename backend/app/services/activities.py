from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.activity import ActivityOperationRole, activity_label
from app.domain.models import Activity, Asset, User
from app.domain.schemas import ActivityFacet, ActivitySummary

MAX_ACTIVITY_DESCRIPTION_LENGTH = 500


def record_activity(
    session: Session,
    *,
    action: str,
    description: str,
    asset: Asset | None = None,
    actor: User | None = None,
    credential_name: str | None = None,
    operation_id: UUID | None = None,
    operation_role: ActivityOperationRole = ActivityOperationRole.SINGLE,
    created_at: datetime | None = None,
) -> Activity:
    activity = Activity(
        asset=asset,
        actor=actor,
        actor_display_name=actor.name if actor else "系统",
        asset_title_snapshot=asset.title if asset else None,
        asset_type_snapshot=asset.type.value if asset else None,
        credential_name=credential_name,
        operation_id=operation_id,
        operation_role=operation_role,
        action=action,
        description=description[:MAX_ACTIVITY_DESCRIPTION_LENGTH],
    )
    if created_at is not None:
        activity.created_at = created_at
    session.add(activity)
    return activity


def activity_summary(activity: Activity) -> ActivitySummary:
    return ActivitySummary(
        id=activity.id,
        asset_id=activity.asset_id,
        asset_title=activity.asset_title_snapshot,
        asset_type=activity.asset_type_snapshot,
        actor_name=activity.actor_display_name,
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
    primary_only: bool = False,
) -> list[ActivitySummary]:
    partition = (
        Activity.asset_id,
        Activity.actor_id,
        Activity.actor_display_name,
        Activity.asset_title_snapshot,
        Activity.asset_type_snapshot,
        Activity.credential_name,
        Activity.action,
        Activity.description,
    )
    ranked = select(
        Activity.id.label("id"),
        Activity.asset_id.label("asset_id"),
        Activity.actor_display_name.label("actor_name"),
        Activity.asset_title_snapshot.label("asset_title"),
        Activity.asset_type_snapshot.label("asset_type"),
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
    if primary_only:
        ranked = ranked.where(Activity.operation_role != ActivityOperationRole.TARGET)
    ranked_activity = ranked.subquery()
    rows = session.execute(
        select(ranked_activity)
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
        .where(Activity.operation_role != ActivityOperationRole.TARGET)
        .group_by(Activity.action)
        .order_by(Activity.action.asc())
    ).all()
    return [
        ActivityFacet(value=action, label=activity_label(action), count=count)
        for action, count in rows
    ]
