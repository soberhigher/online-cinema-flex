from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
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
from app.services.payments import PaymentService

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.get(
    "",
    response_model=list[PaymentOut],
    responses={401: {"description": "Authentication required"}},
    summary="My payment history",
)
async def payment_history(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    """The caller's payments, newest first: amount, status, creation time, order and paid items."""
    return await PaymentService(session).history(user.id)


@router.get("/success", summary="Stripe success redirect", response_model=PaymentResultResponse)
async def payment_success(order_id: UUID | None = None) -> dict[str, object]:
    """Stripe redirects here after a completed checkout; the webhook records the payment."""
    return {
        "status": "success",
        "message": "Thank you! Your payment is being confirmed; the movies appear in your "
        "orders once Stripe notifies us.",
        "order_id": order_id,
    }


@router.get("/cancel", summary="Stripe cancel redirect", response_model=PaymentResultResponse)
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
    summary="Start a Stripe checkout",
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
    """Creates a Stripe Checkout session for a pending order and returns its URL. Returns 409 if
    the order is not pending or movie prices changed since ordering (create a new order then).
    """
    return await PaymentService(session).checkout(user.id, order_id)


@router.post(
    "/webhook",
    summary="Stripe webhook",
    response_model=WebhookResponse,
    responses={
        400: {"description": "Invalid Stripe signature, payload or order owner"},
        409: {"description": "Payment event conflicts with an existing payment"},
        422: {"description": "Request validation failed"},
    },
)
async def webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    signature: Annotated[str, Header(alias="Stripe-Signature")],
) -> dict[str, object]:
    """Receives signed Stripe events. A successful checkout records the payment, marks the order
    paid and emails a confirmation; an expired or failed checkout cancels the order. Events are
    idempotent by Stripe event id. Not for direct use.
    """
    payload = await request.body()
    return await PaymentService(session).webhook(payload, signature)
