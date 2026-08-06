from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_session
from app.domain.schemas import ArchiveHealthSummary, ScanRunSummary, UnclaimedFileSummary
from app.services.archive import StorageScanError, archive_health, scan_storage
from app.services.unclaimed import list_unclaimed_files

router = APIRouter(prefix="/archive", tags=["archive"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.get("/health")
def health(session: SessionDependency) -> ArchiveHealthSummary:
    return archive_health(session, settings.storage_root)


@router.get("/unclaimed")
def unclaimed_files(session: SessionDependency) -> list[UnclaimedFileSummary]:
    return list_unclaimed_files(session)


@router.post("/scans", status_code=status.HTTP_201_CREATED)
def create_scan(session: SessionDependency) -> ScanRunSummary:
    try:
        result = scan_storage(session, settings.storage_root)
        session.commit()
        return result
    except StorageScanError:
        session.commit()
        raise HTTPException(status_code=409, detail="存储根不可用，无法执行扫描。") from None
    except Exception:
        session.rollback()
        raise
