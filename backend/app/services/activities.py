from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.activity import activity_label
from app.domain.models import Activity
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
