from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain.models import PersonalAccessToken, User
from app.services.access_tokens import AccessTokenConfigurationError, authenticate_access_token
from app.services.accounts import get_active_account
from app.services.security import read_session_token

SessionDependency = Annotated[Session, Depends(get_session)]


def require_admin(
    session: SessionDependency, x_sage_session: Annotated[str | None, Header()] = None
) -> User:
    if not x_sage_session:
        raise HTTPException(status_code=401, detail="请先登录。")
    username = read_session_token(x_sage_session)
    if not username:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录。")
    user = get_active_account(session, username)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="当前账号没有管理员权限。")
    return user


AdminDependency = Annotated[User, Depends(require_admin)]


@dataclass(frozen=True)
class AgentPrincipal:
    user: User
    token: PersonalAccessToken


def require_agent(
    session: SessionDependency,
    authorization: Annotated[str | None, Header()] = None,
) -> AgentPrincipal:
    scheme, _, plaintext = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not plaintext:
        raise HTTPException(status_code=401, detail="请使用 Bearer 访问令牌。")
    try:
        token = authenticate_access_token(session, plaintext)
    except AccessTokenConfigurationError as error:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(error)) from None
    if not token:
        raise HTTPException(status_code=401, detail="访问令牌无效、已过期或已撤销。")
    session.commit()
    return AgentPrincipal(user=token.user, token=token)


def require_agent_scope(scope: str):
    def dependency(principal: Annotated[AgentPrincipal, Depends(require_agent)]) -> AgentPrincipal:
        if scope not in principal.token.scopes:
            raise HTTPException(status_code=403, detail=f"访问令牌缺少权限：{scope}")
        return principal

    return dependency
