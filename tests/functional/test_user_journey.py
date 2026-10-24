"""End-to-end path of a customer through the public API."""

import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
import stripe
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import seed_catalog
from app.models.accounts import User
from tests.helpers import STRONG_PASSWORD, checkout_event, queued_emails, signed_webhook


@pytest.mark.asyncio
async def test_register_activate_buy_and_see_payment(
    client: AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with db_factory() as session:
        await seed_catalog(session)

    credentials = {"email": "viewer@example.com", "password": STRONG_PASSWORD}
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await client.post("/api/v1/auth/login", json=credentials)).status_code == 403

    to, _, body = queued_emails[-1]
    assert to == "viewer@example.com"
    token = re.search(r"token=([\w-]+)", body)
    assert token is not None
    activation = await client.get("/api/v1/auth/activate", params={"token": token.group(1)})
    assert activation.status_code == 200

    login = await client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    genres = (await client.get("/api/v1/movies/genres")).json()
    sci_fi = next(genre for genre in genres if genre["name"] == "Sci-Fi")
    assert sci_fi["movie_count"] == 3
    found = await client.get("/api/v1/movies/", params={"search": "nolan", "sort": "imdb"})
    assert found.json()["total"] == 3
    movie = found.json()["items"][0]
    assert movie["name"] == "Interstellar"

    assert (
        await client.post(f"/api/v1/cart/items/{movie['id']}", headers=headers)
    ).status_code == 201
    cart = await client.get("/api/v1/cart", headers=headers)
    assert Decimal(cart.json()["total"]) == Decimal(movie["price"])

    created = await client.post("/api/v1/orders", headers=headers)
    assert created.status_code == 201
    order = created.json()["order"]
    assert order["status"] == "pending"
    assert (await client.get("/api/v1/cart", headers=headers)).json()["items"] == []

    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **_: SimpleNamespace(id="cs_journey", url="https://checkout.stripe.test/journey"),
    )
    checkout = await client.post(f"/api/v1/payments/checkout/{order['id']}", headers=headers)
    assert checkout.json()["checkout_url"] == "https://checkout.stripe.test/journey"

    me = await client.get("/api/v1/admin/users", headers=headers)
    assert me.status_code == 403
    async with db_factory() as session:
        user_id = (await session.scalars(select(User.id).where(User.email == to))).one()
    cents = int(Decimal(order["total_amount"]) * 100)
    payload, signature = signed_webhook(
        checkout_event("evt_journey", "cs_journey", order["id"], user_id, cents)
    )
    webhook = await client.post("/api/v1/payments/webhook", content=payload, headers=signature)
    assert webhook.status_code == 200

    paid = await client.get(f"/api/v1/orders/{order['id']}", headers=headers)
    assert paid.json()["status"] == "paid"
    [payment] = (await client.get("/api/v1/payments", headers=headers)).json()
    assert payment["order_id"] == order["id"]
    assert payment["status"] == "successful"
    assert payment["items"][0]["title"] == "Interstellar"
    assert queued_emails[-1][1] == "Online Cinema payment confirmation"
    assert (
        await client.post(f"/api/v1/cart/items/{movie['id']}", headers=headers)
    ).status_code == 409
