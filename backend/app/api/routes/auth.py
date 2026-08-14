from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import AdminDependency
from app.db.session import get_session
from app.domain.schemas import (
    AccessTokenCreatedResponse,
    AccessTokenCreateRequest,
    AccessTokenSummary,
    AccountCreateRequest,
    AccountLoginRequest,
    AccountLoginResponse,
    AccountSummary,
    AccountUpdateRequest,
    InstanceSetupStatus,
)
from app.services.access_tokens import (
    AccessTokenConfigurationError,
    AccessTokenNotFoundError,
    create_access_token,
    list_access_tokens,
    revoke_access_token,
)
from app.services.accounts import (
    AccountAuthenticationConfigurationError,
    AccountConflictError,
    AccountLoginError,
    AccountNotFoundError,
    AccountSetupConflictError,
    account_summary,
    create_admin_account,
    initialize_admin_account,
    instance_setup_status,
    list_admin_accounts,
    login_account,
    update_admin_account,
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
    except AccountAuthenticationConfigurationError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from None


@router.get("/setup-status")
def setup_status(session: SessionDependency) -> InstanceSetupStatus:
    initialized, authentication_ready = instance_setup_status(session)
    return InstanceSetupStatus(
        initialized=initialized,
        authentication_ready=authentication_ready,
    )


@router.post("/setup", status_code=status.HTTP_201_CREATED)
def setup(payload: AccountCreateRequest, session: SessionDependency) -> AccountLoginResponse:
    try:
        account, session_token = initialize_admin_account(session, payload)
        session.commit()
        return AccountLoginResponse(**account.model_dump(), session_token=session_token)
    except AccountSetupConflictError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except AccountAuthenticationConfigurationError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="实例已经完成初始化，请使用管理员账号登录。",
        ) from None
    except Exception:
        session.rollback()
        raise


@router.get("/me")
def me(current_user: AdminDependency) -> AccountSummary:
    return account_summary(current_user)


@router.get("/admin-accounts")
def admin_accounts(session: SessionDependency, _: AdminDependency) -> list[AccountSummary]:
    result = list_admin_accounts(session)
    session.commit()
    return result


@router.post("/admin-accounts", status_code=status.HTTP_201_CREATED)
def create_admin(
    payload: AccountCreateRequest, session: SessionDependency, _: AdminDependency
) -> AccountSummary:
    try:
        result = create_admin_account(session, payload)
        session.commit()
        return result
    except (AccountConflictError, IntegrityError):
        session.rollback()
        raise HTTPException(status_code=409, detail="账号名或邮箱已被使用。") from None
    except Exception:
        session.rollback()
        raise


@router.patch("/admin-accounts/{username}")
def update_admin(
    username: str,
    payload: AccountUpdateRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> AccountSummary:
    try:
        result = update_admin_account(session, username, payload, actor=current_user)
        session.commit()
        return result
    except AccountNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="账号不存在。") from None
    except AccountConflictError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise


@router.get("/access-tokens")
def access_tokens(
    session: SessionDependency, current_user: AdminDependency
) -> list[AccessTokenSummary]:
    return list_access_tokens(session, current_user)


@router.post("/access-tokens", status_code=status.HTTP_201_CREATED)
def create_token(
    payload: AccessTokenCreateRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> AccessTokenCreatedResponse:
    try:
        result = create_access_token(session, current_user, payload)
        session.commit()
        return result
    except AccessTokenConfigurationError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise


@router.delete("/access-tokens/{token_id}")
def revoke_token(
    token_id: UUID,
    session: SessionDependency,
    current_user: AdminDependency,
) -> AccessTokenSummary:
    try:
        result = revoke_access_token(session, current_user, token_id)
        session.commit()
        return result
    except AccessTokenNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="访问令牌不存在。") from None
    except Exception:
        session.rollback()
        raise
