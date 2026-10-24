from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.models.accounts import User
from app.schemas.payments import (
    CheckoutResponse,
    PaymentOut,
    PaymentResultResponse,
    WebhookResponse,
)
from app.services.auth import AuthError
from app.services.payments import PaymentService

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.get(
    "", response_model=list[PaymentOut], responses={401: {"description": "Authentication required"}}
)
async def payment_history(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    return await PaymentService(session).history(user.id)


@router.get("/success", response_model=PaymentResultResponse)
async def payment_success(order_id: UUID | None = None) -> dict[str, object]:
    """Stripe redirects here after a completed checkout; the webhook records the payment."""
    return {
        "status": "success",
        "message": "Thank you! Your payment is being confirmed; the movies appear in your "
        "orders once Stripe notifies us.",
        "order_id": order_id,
    }


@router.get("/cancel", response_model=PaymentResultResponse)
async def payment_cancel(order_id: UUID | None = None) -> dict[str, object]:
    """Stripe redirects here when the customer abandons checkout."""
    return {
        "status": "canceled",
        "message": "Payment was not completed. Your order is still pending: please try "
        "another payment method or try again later.",
        "order_id": order_id,
    }


@router.post(
    "/checkout/{order_id}",
    response_model=CheckoutResponse,
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Order not found or not owned by caller"},
        409: {"description": "Order is not payable or prices changed"},
        422: {"description": "Request validation failed"},
    },
)
async def checkout(
    order_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str | None]:
    try:
        return await PaymentService(session).checkout(user.id, order_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    responses={
        400: {"description": "Invalid Stripe signature, payload, amount, or owner"},
        409: {"description": "Payment event conflicts with an existing payment"},
        422: {"description": "Request validation failed"},
    },
)
async def webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    signature: Annotated[str, Header(alias="Stripe-Signature")],
) -> dict[str, object]:
    payload = await request.body()
    try:
        return await PaymentService(session).webhook(payload, signature)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
