from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
import stripe
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.accounts import UserGroupEnum
from app.models.catalog import Movie
from tests.helpers import bearer, checkout_event, create_user, signed_webhook


async def create_movie(factory: async_sessionmaker[AsyncSession], name: str, price: str) -> UUID:
    async with factory() as session:
        movie = Movie(name=name, year=2000, time=100, imdb=Decimal("7.0"), price=Decimal(price))
        session.add(movie)
        await session.commit()
        return movie.id


async def paid_order(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    user_id: UUID,
    movie_id: UUID,
    cents: int,
    suffix: str,
) -> dict[str, Any]:
    headers = bearer(user_id)
    assert (await client.post(f"/api/v1/cart/items/{movie_id}", headers=headers)).status_code == 201
    order = (await client.post("/api/v1/orders", headers=headers)).json()["order"]
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **_: SimpleNamespace(id=f"cs_{suffix}", url="https://checkout.test"),
    )
    checkout = await client.post(f"/api/v1/payments/checkout/{order['id']}", headers=headers)
    assert checkout.status_code == 200
    payload, signature = signed_webhook(
        checkout_event(f"evt_{suffix}", f"cs_{suffix}", order["id"], user_id, cents)
    )
    webhook = await client.post("/api/v1/payments/webhook", content=payload, headers=signature)
    assert webhook.status_code == 200
    return dict(order)


@pytest.mark.asyncio
async def test_order_has_created_at_and_user_sees_payment_history(
    client: AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_factory, "buyer@example.com")
    other_id = await create_user(db_factory, "other@example.com")
    movie_id = await create_movie(db_factory, "Heat", "4.50")
    order = await paid_order(client, monkeypatch, user_id, movie_id, 450, "one")
    assert order["created_at"]

    history = await client.get("/api/v1/payments", headers=bearer(user_id))
    assert history.status_code == 200
    [payment] = history.json()
    assert payment["order_id"] == order["id"]
    assert payment["status"] == "successful"
    assert Decimal(payment["amount"]) == Decimal("4.50")
    assert payment["created_at"]
    assert payment["items"][0]["title"] == "Heat"

    assert (await client.get("/api/v1/payments", headers=bearer(other_id))).json() == []
    assert (await client.get("/api/v1/payments")).status_code == 401
    orders = await client.get("/api/v1/orders", headers=bearer(user_id))
    assert orders.json()[0]["created_at"]


@pytest.mark.asyncio
async def test_stripe_redirect_pages() -> None:
    from httpx import ASGITransport

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        success = await client.get("/api/v1/payments/success")
        cancel = await client.get("/api/v1/payments/cancel")
    assert success.status_code == 200
    assert success.json()["status"] == "success"
    assert cancel.status_code == 200
    assert "another payment method" in cancel.json()["message"]


@pytest.mark.asyncio
async def test_staff_can_filter_orders_payments_and_view_carts(
    client: AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buyer = await create_user(db_factory, "buyer@example.com")
    browser = await create_user(db_factory, "browser@example.com")
    moderator = await create_user(db_factory, "mod@example.com", UserGroupEnum.MODERATOR)
    staff = bearer(moderator, UserGroupEnum.MODERATOR)
    first = await create_movie(db_factory, "Alien", "3.00")
    second = await create_movie(db_factory, "Aliens", "5.00")
    await paid_order(client, monkeypatch, buyer, first, 300, "a")
    await client.post(f"/api/v1/cart/items/{second}", headers=bearer(buyer))
    await client.post("/api/v1/orders", headers=bearer(buyer))
    await client.post(f"/api/v1/cart/items/{second}", headers=bearer(browser))

    for path in ("orders", "payments", f"carts/{buyer}"):
        response = await client.get(f"/api/v1/admin/{path}", headers=bearer(buyer))
        assert response.status_code == 403

    orders = await client.get("/api/v1/admin/orders", headers=staff)
    assert orders.status_code == 200
    assert orders.json()["total"] == 2
    pending = await client.get(
        "/api/v1/admin/orders", params={"status": "pending", "user_id": str(buyer)}, headers=staff
    )
    assert pending.json()["total"] == 1
    assert pending.json()["items"][0]["user_id"] == str(buyer)
    future = await client.get(
        "/api/v1/admin/orders", params={"date_from": "2999-01-01T00:00:00Z"}, headers=staff
    )
    assert future.json()["total"] == 0
    past = await client.get(
        "/api/v1/admin/orders", params={"date_to": "2000-01-01T00:00:00Z"}, headers=staff
    )
    assert past.json()["total"] == 0
    paged = await client.get("/api/v1/admin/orders", params={"per_page": 1}, headers=staff)
    assert len(paged.json()["items"]) == 1

    payments = await client.get(
        "/api/v1/admin/payments",
        params={
            "status": "successful",
            "user_id": str(buyer),
            "date_from": "2000-01-01T00:00:00Z",
            "date_to": "2999-01-01T00:00:00Z",
        },
        headers=staff,
    )
    assert payments.status_code == 200
    assert payments.json()["total"] == 1
    assert payments.json()["items"][0]["user_id"] == str(buyer)
    refunded = await client.get(
        "/api/v1/admin/payments", params={"status": "refunded"}, headers=staff
    )
    assert refunded.json()["total"] == 0

    cart = await client.get(f"/api/v1/admin/carts/{browser}", headers=staff)
    assert cart.status_code == 200
    assert cart.json()["items"][0]["title"] == "Aliens"
    missing = await client.get(
        "/api/v1/admin/carts/00000000-0000-0000-0000-000000000000", headers=staff
    )
    assert missing.status_code == 404
