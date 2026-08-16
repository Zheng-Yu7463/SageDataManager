from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import AdminDependency
from app.db.session import get_session
from app.domain.models import User
from app.domain.schemas import (
    AccessTokenCreatedResponse,
    AccessTokenCreateRequest,
    AccessTokenSummary,
    AccountCreateRequest,
    AccountInvitationAcceptRequest,
    AccountInvitationCreatedResponse,
    AccountInvitationCreateRequest,
    AccountInvitationStatus,
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
    AccountInvitationInvalidError,
    AccountInvitationValidationError,
    AccountLoginError,
    AccountNotFoundError,
    AccountPermissionError,
    AccountSetupConflictError,
    InvitationPurpose,
    accept_account_invitation,
    account_summary,
    create_admin_invitation,
    get_account_invitation,
    initialize_admin_account,
    instance_setup_status,
    list_admin_accounts,
    login_account,
    renew_admin_invitation,
    update_admin_account,
)

router = APIRouter(prefix="/auth", tags=["auth"])
SessionDependency = Annotated[Session, Depends(get_session)]
InvitationTokenHeader = Annotated[
    str,
    Header(
        alias="X-Sage-Invitation-Token",
        min_length=60,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
]


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
    payload: AccountInvitationCreateRequest,
    session: SessionDependency,
    current_user: AdminDependency,
) -> AccountInvitationCreatedResponse:
    try:
        result = create_admin_invitation(session, payload, actor=current_user)
        session.commit()
        return result
    except AccountConflictError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="账号名已被使用。") from None
    except Exception:
        session.rollback()
        raise


@router.post("/admin-accounts/{username}/registration-invitation")
def renew_registration_invitation(
    username: str,
    session: SessionDependency,
    current_user: AdminDependency,
) -> AccountInvitationCreatedResponse:
    return _renew_invitation(session, username, current_user, "registration")


@router.post("/admin-accounts/{username}/recovery-invitation")
def create_recovery_invitation(
    username: str,
    session: SessionDependency,
    current_user: AdminDependency,
) -> AccountInvitationCreatedResponse:
    return _renew_invitation(session, username, current_user, "recovery")


def _renew_invitation(
    session: Session, username: str, current_user: User, purpose: InvitationPurpose
) -> AccountInvitationCreatedResponse:
    try:
        result = renew_admin_invitation(session, username, actor=current_user, purpose=purpose)
        session.commit()
        return result
    except AccountNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail="账号不存在。") from None
    except AccountPermissionError as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from None
    except AccountConflictError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        session.rollback()
        raise


def _invitation_status(session: Session, token: str) -> AccountInvitationStatus:
    try:
        return get_account_invitation(session, token)
    except AccountInvitationInvalidError:
        raise HTTPException(status_code=404, detail="注册链接无效或已失效。") from None


@router.get("/invitations")
def invitation_status(
    session: SessionDependency,
    token: InvitationTokenHeader,
) -> AccountInvitationStatus:
    return _invitation_status(session, token)


def _accept_invitation(
    session: Session, token: str, payload: AccountInvitationAcceptRequest
) -> AccountLoginResponse:
    try:
        account, session_token = accept_account_invitation(session, token, payload)
        session.commit()
        return AccountLoginResponse(**account.model_dump(), session_token=session_token)
    except AccountInvitationInvalidError:
        session.rollback()
        raise HTTPException(status_code=404, detail="注册链接无效或已失效。") from None
    except AccountInvitationValidationError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from None
    except AccountConflictError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from None
    except AccountAuthenticationConfigurationError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="邮箱已被使用。") from None
    except Exception:
        session.rollback()
        raise


@router.post("/invitations/accept")
def accept_invitation(
    payload: AccountInvitationAcceptRequest,
    session: SessionDependency,
    token: InvitationTokenHeader,
) -> AccountLoginResponse:
    return _accept_invitation(session, token, payload)


@router.get("/invitations/{token}", include_in_schema=False)
def legacy_invitation_status(
    token: Annotated[str, Path(min_length=60, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")],
    session: SessionDependency,
) -> AccountInvitationStatus:
    return _invitation_status(session, token)


@router.post("/invitations/{token}/accept", include_in_schema=False)
def legacy_accept_invitation(
    token: Annotated[str, Path(min_length=60, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")],
    payload: AccountInvitationAcceptRequest,
    session: SessionDependency,
) -> AccountLoginResponse:
    return _accept_invitation(session, token, payload)


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
    except AccountPermissionError as error:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from None
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
