"""Shared helpers for integration and functional tests."""

import hashlib
import hmac
import json
import os
import time
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models.accounts import User, UserGroup, UserGroupEnum

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://cinema:changeme@localhost:5432/cinema_test",
)
STRONG_PASSWORD = "StrongPassword9!"

# Emails "sent" during a test: (to, subject, body). Filled by the client fixture.
queued_emails: list[tuple[str, str, str]] = []


async def create_user(
    factory: async_sessionmaker[AsyncSession],
    email: str,
    role: UserGroupEnum = UserGroupEnum.USER,
    *,
    password: str | None = None,
    active: bool = True,
) -> UUID:
    """Insert a user directly into the database and return its id."""
    async with factory() as session:
        group = await session.scalar(select(UserGroup).where(UserGroup.name == role))
        assert group is not None
        user = User(
            email=email,
            password_hash=hash_password(password) if password else "unused",
            group_id=group.id,
            is_active=active,
        )
        session.add(user)
        await session.commit()
        return user.id


def bearer(user_id: UUID, role: UserGroupEnum = UserGroupEnum.USER) -> dict[str, str]:
    token, _ = create_access_token(str(user_id), role.value)
    return {"Authorization": f"Bearer {token}"}


def signed_webhook(event: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    """Serialize a Stripe event and sign it the way Stripe does."""
    payload = json.dumps(event, separators=(",", ":")).encode()
    timestamp = int(time.time())
    signature = hmac.new(
        get_settings().stripe_webhook_secret.encode(),
        str(timestamp).encode() + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    return payload, {"Stripe-Signature": f"t={timestamp},v1={signature}"}


def checkout_event(
    event_id: str,
    session_id: str,
    order_id: str,
    user_id: UUID,
    amount_cents: int,
    *,
    event_type: str = "checkout.session.completed",
    payment_status: str = "paid",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": {
                "id": session_id,
                "amount_total": amount_cents,
                "payment_status": payment_status,
                "metadata": {"order_id": order_id, "user_id": str(user_id)},
            }
        },
    }
