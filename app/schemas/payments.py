from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class CheckoutResponse(BaseModel):
    checkout_url: str | None
    checkout_id: str


class WebhookResponse(BaseModel):
    received: bool
    duplicate: bool | None = None
    ignored: bool | None = None
    pending: bool | None = None
    payment_id: str | None = None
    status: str | None = None


class PaymentItemOut(BaseModel):
    movie_id: UUID
    title: str
    price_at_payment: Decimal


class PaymentOut(BaseModel):
    id: UUID
    order_id: UUID
    amount: Decimal
    status: str
    created_at: datetime
    items: list[PaymentItemOut]


class AdminPaymentOut(PaymentOut):
    user_id: UUID


class AdminPaymentPage(BaseModel):
    items: list[AdminPaymentOut]
    total: int
    page: int
    per_page: int


class PaymentResultResponse(BaseModel):
    status: str
    message: str
    order_id: UUID | None = None
