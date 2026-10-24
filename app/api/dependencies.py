from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials, OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.rate_limit import check_rate_limit
from app.models.accounts import User, UserGroupEnum
from app.services.auth import AuthService
from app.services.errors import ServiceError

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
docs_basic_scheme = HTTPBasic(auto_error=False, realm="Online Cinema API docs")


async def get_auth_service(session: AsyncSession = Depends(get_session)) -> AuthService:
    return AuthService(session)


async def get_current_user(
    token: str = Depends(oauth2_scheme), service: AuthService = Depends(get_auth_service)
) -> User:
    return await service.current_user(token)


def require_roles(*roles: UserGroupEnum) -> Callable[..., Any]:
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.group.name not in roles and not (
            user.group.name == UserGroupEnum.ADMIN and UserGroupEnum.MODERATOR in roles
        ):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return dependency


async def get_docs_user(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(docs_basic_scheme),
    service: AuthService = Depends(get_auth_service),
) -> User:
    """Gate for /docs and /openapi.json.

    A browser cannot attach a Bearer header to a page load, so the docs also accept
    HTTP Basic with the account email and password (the browser shows a login prompt).
    API clients may keep using `Authorization: Bearer <access token>`.
    """
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        try:
            return await service.current_user(authorization[7:].strip())
        except ServiceError:
            pass
    elif credentials is not None:
        check_rate_limit(request, "docs-login", 10, 60)
        user = await service.authenticate(credentials.username, credentials.password)
        if user is not None:
            return user
    raise HTTPException(
        status_code=401,
        detail="Sign in with your account email and password to view the API docs",
        headers={"WWW-Authenticate": 'Basic realm="Online Cinema API docs"'},
    )
