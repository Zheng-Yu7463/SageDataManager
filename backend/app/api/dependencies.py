from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain.models import PersonalAccessToken, User
from app.services.access_tokens import (
    AccessTokenConfigurationError,
    authenticate_access_token,
    record_access_token_use,
)
from app.services.accounts import get_active_account
from app.services.security import read_session_claims

SessionDependency = Annotated[Session, Depends(get_session)]
agent_bearer = HTTPBearer(auto_error=False)


def require_admin(
    session: SessionDependency, x_sage_session: Annotated[str | None, Header()] = None
) -> User:
    if not x_sage_session:
        raise HTTPException(status_code=401, detail="请先登录。")
    claims = read_session_claims(x_sage_session)
    if not claims:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录。")
    user = get_active_account(session, claims.username)
    if not user or user.session_generation != claims.generation:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录。")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="当前账号没有管理员权限。")
    return user


AdminDependency = Annotated[User, Depends(require_admin)]


def require_instance_owner(current_user: AdminDependency) -> User:
    if not current_user.is_instance_owner:
        raise HTTPException(status_code=403, detail="只有实例所有者可以执行系统更新。")
    return current_user


InstanceOwnerDependency = Annotated[User, Depends(require_instance_owner)]


@dataclass(frozen=True)
class AgentPrincipal:
    user: User
    token: PersonalAccessToken


def require_agent(
    session: SessionDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(agent_bearer)],
) -> AgentPrincipal:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="请使用 Bearer 访问令牌。")
    try:
        token = authenticate_access_token(session, credentials.credentials)
    except AccessTokenConfigurationError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from None
    if not token:
        raise HTTPException(status_code=401, detail="访问令牌无效、已过期或已撤销。")
    return AgentPrincipal(user=token.user, token=token)


def require_agent_scope(scope: str):
    def dependency(
        session: SessionDependency,
        principal: Annotated[AgentPrincipal, Depends(require_agent)],
    ) -> AgentPrincipal:
        if scope not in principal.token.scopes:
            raise HTTPException(status_code=403, detail=f"访问令牌缺少权限：{scope}")
        record_access_token_use(session, principal.token)
        return principal

    return dependency
