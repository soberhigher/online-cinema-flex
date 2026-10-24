from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.models.accounts import User
from app.schemas.payments import CheckoutResponse, WebhookResponse
from app.services.auth import AuthError
from app.services.payments import PaymentService

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post("/checkout/{order_id}", response_model=CheckoutResponse)
async def checkout(
    order_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str | None]:
    try:
        return await PaymentService(session).checkout(user.id, order_id)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/webhook", response_model=WebhookResponse)
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
