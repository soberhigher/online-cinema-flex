from pydantic import BaseModel


class CheckoutResponse(BaseModel):
    checkout_url: str | None
    checkout_id: str


class WebhookResponse(BaseModel):
    received: bool
    duplicate: bool | None = None
    ignored: bool | None = None
    payment_id: str | None = None
    status: str | None = None
