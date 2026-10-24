from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_roles
from app.core.database import get_session
from app.models.accounts import UserGroupEnum
from app.models.payments import PaymentStatus
from app.models.purchases import OrderStatus
from app.schemas.admin import AdminUserOut, AdminUserPage, GroupChange
from app.schemas.payments import AdminPaymentPage
from app.schemas.purchases import AdminOrderPage, CartResponse
from app.services.admin import AdminService
from app.services.auth import AuthError

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["user administration"],
    dependencies=[Depends(require_roles(UserGroupEnum.ADMIN))],
)

staff_router = APIRouter(
    prefix="/api/v1/admin",
    tags=["sales administration"],
    dependencies=[Depends(require_roles(UserGroupEnum.MODERATOR))],
)

STAFF_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication required or access token is invalid"},
    403: {"description": "Only moderators and administrators may view sales data"},
    422: {"description": "Request validation failed"},
}

ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication required or access token is invalid"},
    403: {"description": "Only administrators may manage users"},
    404: {"description": "User not found"},
    422: {"description": "Request validation failed"},
}


async def service(session: Annotated[AsyncSession, Depends(get_session)]) -> AdminService:
    return AdminService(session)


@router.get("/users", summary="List users", response_model=AdminUserPage, responses=ADMIN_ERRORS)
async def list_users(
    service: Annotated[AdminService, Depends(service)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    email: Annotated[str | None, Query(description="Case-insensitive email substring")] = None,
) -> dict[str, Any]:
    """ADMIN. Paginated user list, optionally filtered by an email substring."""
    return await service.list_users(page, per_page, email)


@router.patch(
    "/users/{user_id}/group",
    summary="Change a user's group",
    response_model=AdminUserOut,
    responses=ADMIN_ERRORS,
)
async def change_group(
    user_id: UUID, body: GroupChange, service: Annotated[AdminService, Depends(service)]
) -> dict[str, Any]:
    """ADMIN. Moves a user to USER, MODERATOR or ADMIN. Takes effect immediately: roles are
    read from the database on every request. Demoting the last admin returns 409.
    """
    try:
        return await service.change_group(user_id, body.group)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post(
    "/users/{user_id}/activate",
    summary="Activate a user",
    response_model=AdminUserOut,
    responses=ADMIN_ERRORS,
)
async def activate_user(
    user_id: UUID, service: Annotated[AdminService, Depends(service)]
) -> dict[str, Any]:
    """ADMIN. Activates an account manually, e.g. when the activation email was lost."""
    try:
        return await service.activate(user_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@staff_router.get(
    "/orders", summary="All orders", response_model=AdminOrderPage, responses=STAFF_ERRORS
)
async def list_orders(
    service: Annotated[AdminService, Depends(service)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    user_id: UUID | None = None,
    status: OrderStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """MODERATOR+. Orders of all users, filterable by user, status and creation time range
    (inclusive), paginated.
    """
    return await service.orders(page, per_page, user_id, status, date_from, date_to)


@staff_router.get(
    "/payments", summary="All payments", response_model=AdminPaymentPage, responses=STAFF_ERRORS
)
async def list_payments(
    service: Annotated[AdminService, Depends(service)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    user_id: UUID | None = None,
    status: PaymentStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """MODERATOR+. Payments of all users, filterable by user, status and creation time range
    (inclusive), paginated.
    """
    return await service.payments(page, per_page, user_id, status, date_from, date_to)


@staff_router.get(
    "/carts/{user_id}",
    summary="A user's cart",
    response_model=CartResponse,
    responses={**STAFF_ERRORS, 404: {"description": "User not found"}},
)
async def user_cart(
    user_id: UUID, service: Annotated[AdminService, Depends(service)]
) -> dict[str, object]:
    """MODERATOR+. The current cart of any user, e.g. for support."""
    try:
        return await service.cart(user_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
