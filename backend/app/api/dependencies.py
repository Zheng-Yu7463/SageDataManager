from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.domain.models import User
from app.services.accounts import get_fixed_account
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
    user = get_fixed_account(session, username)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="当前账号没有管理员权限。")
    return user


AdminDependency = Annotated[User, Depends(require_admin)]
