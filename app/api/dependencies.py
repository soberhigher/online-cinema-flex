from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.models.accounts import User, UserGroupEnum
from app.services.auth import AuthError, AuthService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


async def get_auth_service(session: AsyncSession = Depends(get_session)) -> AuthService:
    return AuthService(session)


async def get_current_user(
    token: str = Depends(oauth2_scheme), service: AuthService = Depends(get_auth_service)
) -> User:
    try:
        return await service.current_user(token)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def require_roles(*roles: UserGroupEnum) -> Callable[..., Any]:
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.group.name not in roles and not (
            user.group.name == UserGroupEnum.ADMIN and UserGroupEnum.MODERATOR in roles
        ):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency
