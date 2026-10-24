from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.models.accounts import User
from app.schemas.purchases import CartResponse, OrderCreationResponse, OrderResponse
from app.services.auth import AuthError
from app.services.purchases import PurchaseService

cart_router = APIRouter(prefix="/api/v1/cart", tags=["shopping cart"])
order_router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication required or access token is invalid"},
    404: {"description": "Cart item or order was not found"},
    409: {"description": "Cart or order state conflicts with this operation"},
    422: {"description": "Request validation failed"},
}


@cart_router.get("", response_model=CartResponse, responses=AUTH_RESPONSES)
async def get_cart(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, object]:
    return await PurchaseService(session).cart(user.id)


@cart_router.post(
    "/items/{movie_id}", status_code=status.HTTP_201_CREATED, responses=AUTH_RESPONSES
)
async def add_item(
    movie_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    try:
        return await PurchaseService(session).add_to_cart(user.id, movie_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@cart_router.delete(
    "/items/{movie_id}", status_code=status.HTTP_204_NO_CONTENT, responses=AUTH_RESPONSES
)
async def remove_item(
    movie_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        await PurchaseService(session).remove_from_cart(user.id, movie_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@cart_router.delete("", status_code=status.HTTP_204_NO_CONTENT, responses=AUTH_RESPONSES)
async def clear_cart(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await PurchaseService(session).clear_cart(user.id)


@order_router.post(
    "",
    response_model=OrderCreationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=AUTH_RESPONSES,
)
async def create_order(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, object]:
    try:
        return await PurchaseService(session).create_order(user.id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@order_router.get("", response_model=list[OrderResponse], responses={401: AUTH_RESPONSES[401]})
async def list_orders(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict[str, object]]:
    return await PurchaseService(session).orders(user.id)


@order_router.get("/{order_id}", response_model=OrderResponse, responses=AUTH_RESPONSES)
async def get_order(
    order_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, object]:
    try:
        return await PurchaseService(session).order(user.id, order_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@order_router.post("/{order_id}/cancel", responses=AUTH_RESPONSES)
async def cancel_order(
    order_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, object]:
    try:
        return await PurchaseService(session).cancel_order(user.id, order_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
