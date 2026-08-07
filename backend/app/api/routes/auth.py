from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import AdminDependency
from app.core.config import settings
from app.db.session import get_session
from app.domain.schemas import (
    AccountLoginRequest,
    AccountLoginResponse,
    AccountSummary,
    RegistrationStatus,
)
from app.services.accounts import (
    AccountLoginError,
    account_summary,
    login_account,
)

router = APIRouter(prefix="/auth", tags=["auth"])
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("/login")
def login(payload: AccountLoginRequest, session: SessionDependency) -> AccountLoginResponse:
    try:
        account, session_token = login_account(session, payload.username, payload.password)
        session.commit()
        return AccountLoginResponse(**account.model_dump(), session_token=session_token)
    except AccountLoginError as error:
        session.rollback()
        raise HTTPException(status_code=401, detail=str(error)) from None


@router.get("/me")
def me(current_user: AdminDependency) -> AccountSummary:
    return account_summary(current_user)


@router.get("/registration-status")
def registration_status() -> RegistrationStatus:
    return RegistrationStatus(enabled=settings.registration_enabled)
