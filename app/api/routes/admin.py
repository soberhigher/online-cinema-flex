from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_roles
from app.core.database import get_session
from app.models.accounts import UserGroupEnum
from app.schemas.admin import AdminUserOut, AdminUserPage, GroupChange
from app.services.admin import AdminService
from app.services.auth import AuthError

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["user administration"],
    dependencies=[Depends(require_roles(UserGroupEnum.ADMIN))],
)

ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication required or access token is invalid"},
    403: {"description": "Only administrators may manage users"},
    404: {"description": "User not found"},
    422: {"description": "Request validation failed"},
}


async def service(session: Annotated[AsyncSession, Depends(get_session)]) -> AdminService:
    return AdminService(session)


@router.get("/users", response_model=AdminUserPage, responses=ADMIN_ERRORS)
async def list_users(
    service: Annotated[AdminService, Depends(service)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    email: Annotated[str | None, Query(description="Case-insensitive email substring")] = None,
) -> dict[str, Any]:
    return await service.list_users(page, per_page, email)


@router.patch("/users/{user_id}/group", response_model=AdminUserOut, responses=ADMIN_ERRORS)
async def change_group(
    user_id: UUID, body: GroupChange, service: Annotated[AdminService, Depends(service)]
) -> dict[str, Any]:
    try:
        return await service.change_group(user_id, body.group)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/users/{user_id}/activate", response_model=AdminUserOut, responses=ADMIN_ERRORS)
async def activate_user(
    user_id: UUID, service: Annotated[AdminService, Depends(service)]
) -> dict[str, Any]:
    try:
        return await service.activate(user_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
