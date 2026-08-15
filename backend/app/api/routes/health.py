from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import SessionDependency
from app.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "sage-data-manager-api"}


@router.get("/ready")
def readiness(session: SessionDependency) -> dict[str, str]:
    try:
        database_revision = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        expected_revision = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    except (OSError, SQLAlchemyError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库或迁移状态不可用。",
        ) from error
    if not database_revision or database_revision != expected_revision:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库迁移版本与应用不一致。",
        )
    return {
        "status": "ready",
        "service": "sage-data-manager-api",
        "release_commit": settings.release_commit,
        "database_revision": database_revision,
    }
