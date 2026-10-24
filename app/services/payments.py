import asyncio
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

import stripe
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.accounts import User
from app.models.payments import Payment, PaymentItem, PaymentStatus
from app.models.purchases import Order, OrderItem, OrderStatus
from app.services.errors import ServiceError
from app.tasks.auth_tasks import send_email

logger = logging.getLogger(__name__)


def payment_data(payment: Payment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "order_id": payment.order_id,
        "user_id": payment.user_id,
        "amount": payment.amount,
        "status": payment.status,
        "created_at": payment.created_at,
        "items": [
            {
                "movie_id": item.movie_id,
                "title": item.movie.name,
                "price_at_payment": item.price_at_payment,
            }
            for item in payment.items
        ],
    }


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def history(self, user_id: UUID) -> list[dict[str, Any]]:
        rows = await self.session.scalars(
            select(Payment)
            .options(selectinload(Payment.items).selectinload(PaymentItem.movie))
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
        )
        return [payment_data(payment) for payment in rows]

    async def checkout(self, user_id: UUID, order_id: UUID) -> dict[str, str | None]:
        order = await self.session.scalar(
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.movie))
            .where(Order.id == order_id, Order.user_id == user_id)
        )
        if order is None:
            raise ServiceError(404, "Order not found")
        if order.status != OrderStatus.PENDING or not order.items:
            raise ServiceError(409, "Only non-empty pending orders can be checked out")
        current_total = sum((item.movie.price for item in order.items), Decimal("0.00"))
        snapshot_total = sum((item.price_at_order for item in order.items), Decimal("0.00"))
        if current_total != snapshot_total or current_total != order.total_amount:
            raise ServiceError(409, "Order prices changed; create a new order")
        stripe.api_key = self.settings.stripe_secret_key
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            payment_method_types=["card"],
            mode="payment",
            success_url=self.settings.stripe_success_url,
            cancel_url=self.settings.stripe_cancel_url,
            client_reference_id=str(order.id),
            metadata={"order_id": str(order.id), "user_id": str(user_id)},
            line_items=[
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": int(item.price_at_order * 100),
                        "product_data": {"name": item.movie.name},
                    },
                }
                for item in order.items
            ],
            idempotency_key=f"checkout:{order.id}",
        )
        order.stripe_checkout_id = session.id
        await self.session.commit()
        return {"checkout_url": session.url, "checkout_id": session.id}

    async def webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.settings.stripe_webhook_secret
            )
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise ServiceError(400, "Invalid Stripe webhook signature or payload") from exc
        event_id = str(event["id"])
        existing = await self.session.scalar(
            select(Payment).where(Payment.stripe_event_id == event_id)
        )
        if existing is not None:
            return {"received": True, "duplicate": True}
        event_type = str(event["type"])
        if event_type not in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.expired",
            "checkout.session.async_payment_failed",
        }:
            return {"received": True, "ignored": True}
        session_data = event["data"]["object"]
        metadata = session_data.get("metadata") or {}
        try:
            order_id = UUID(str(metadata["order_id"]))
            user_id = UUID(str(metadata["user_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise ServiceError(400, "Stripe session has invalid order metadata") from exc
        order = await self.session.scalar(
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.movie))
            .where(Order.id == order_id)
            .with_for_update()
        )
        if order is None or order.user_id != user_id:
            raise ServiceError(400, "Stripe session references an invalid order owner")
        is_paid_event = event_type in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        }
        is_success = is_paid_event and session_data.get("payment_status") == "paid"
        checkout_id = str(session_data["id"])
        if not is_paid_event and order.stripe_checkout_id not in (None, checkout_id):
            # An older session expired while a newer one is still open for this order.
            return {"received": True, "ignored": True}
        existing_payment: Payment | None = None
        if order.status != OrderStatus.PENDING:
            existing_payment = await self.session.scalar(
                select(Payment).where(Payment.order_id == order_id)
            )
            if existing_payment is not None and (
                existing_payment.status == PaymentStatus.SUCCESSFUL or not is_success
            ):
                return {"received": True, "duplicate": True}
            if not is_success:
                return {"received": True, "ignored": True}
            # The customer was charged after the order left "pending" (e.g. canceled while
            # the Stripe page was open): honour the payment instead of dropping it.
            logger.warning(
                "Successful payment for %s order %s; recording it and marking paid",
                order.status,
                order_id,
            )
        if is_paid_event and not is_success:
            return {"received": True, "pending": True}
        expected_cents = int(order.total_amount * 100)
        if is_success and session_data.get("amount_total") != expected_cents:
            raise ServiceError(400, "Stripe amount does not match order total")
        if existing_payment is not None:
            # A canceled attempt was charged after all: upgrade its record.
            payment = existing_payment
            payment.status = PaymentStatus.SUCCESSFUL
            payment.stripe_event_id = event_id
            payment.stripe_checkout_id = checkout_id
            payment.amount = order.total_amount
        else:
            payment = Payment(
                order_id=order.id,
                user_id=user_id,
                status=PaymentStatus.SUCCESSFUL if is_success else PaymentStatus.CANCELED,
                amount=order.total_amount,
                stripe_checkout_id=checkout_id,
                stripe_event_id=event_id,
                items=[
                    PaymentItem(movie_id=item.movie_id, price_at_payment=item.price_at_order)
                    for item in order.items
                ],
            )
        if is_success:
            order.status = OrderStatus.PAID
        else:
            order.status = OrderStatus.CANCELED
        self.session.add(payment)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            duplicate = await self.session.scalar(
                select(Payment.id).where(Payment.order_id == order_id)
            )
            if duplicate is not None:
                return {"received": True, "duplicate": True}
            raise ServiceError(409, "Payment event conflicts with existing payment") from exc
        if is_success:
            user = await self.session.get(User, user_id)
            if user is not None:
                try:
                    send_email.delay(
                        user.email,
                        "Online Cinema payment confirmation",
                        f"Payment {payment.id} for order {order_id} was successful.",
                    )
                except Exception:
                    logger.exception("Failed to queue payment confirmation for order %s", order_id)
        return {"received": True, "payment_id": str(payment.id), "status": payment.status}
