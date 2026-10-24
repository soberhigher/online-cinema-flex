from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
import stripe
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.accounts import UserGroupEnum
from app.models.catalog import Movie
from app.models.purchases import Cart
from app.services.purchases import PurchaseService
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


@pytest.mark.asyncio
async def test_payment_for_canceled_order_is_recorded(
    client: AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_factory, "late@example.com")
    headers = bearer(user_id)
    movie_id = await create_movie(db_factory, "Late Payment", "2.00")
    await client.post(f"/api/v1/cart/items/{movie_id}", headers=headers)
    order = (await client.post("/api/v1/orders", headers=headers)).json()["order"]
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **_: SimpleNamespace(id="cs_late", url="https://checkout.test"),
    )
    assert (
        await client.post(f"/api/v1/payments/checkout/{order['id']}", headers=headers)
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/orders/{order['id']}/cancel", headers=headers)
    ).status_code == 200

    expired, expired_sig = signed_webhook(
        checkout_event(
            "evt_late_expired",
            "cs_late",
            order["id"],
            user_id,
            200,
            event_type="checkout.session.expired",
            payment_status="unpaid",
        )
    )
    ignored = await client.post("/api/v1/payments/webhook", content=expired, headers=expired_sig)
    assert ignored.status_code == 200
    assert ignored.json()["ignored"] is True

    async with db_factory() as session:
        movie = await session.get(Movie, movie_id)
        assert movie is not None
        movie.price = Decimal("9.00")
        await session.commit()

    paid, paid_sig = signed_webhook(
        checkout_event("evt_late_paid", "cs_late", order["id"], user_id, 200)
    )
    response = await client.post("/api/v1/payments/webhook", content=paid, headers=paid_sig)
    assert response.status_code == 200
    assert response.json()["status"] == "successful"
    detail = await client.get(f"/api/v1/orders/{order['id']}", headers=headers)
    assert detail.json()["status"] == "paid"
    [payment] = (await client.get("/api/v1/payments", headers=headers)).json()
    assert Decimal(payment["amount"]) == Decimal("2.00")


@pytest.mark.asyncio
async def test_first_cart_insert_race_reuses_existing_cart(
    db_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await create_user(db_factory, "racer@example.com")
    async with db_factory() as session:
        service = PurchaseService(session)
        original_scalar = session.scalar
        calls = 0

        async def racing_scalar(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                # Another request inserts the cart right after our lookup missed it.
                async with db_factory() as other:
                    other.add(Cart(user_id=user_id))
                    await other.commit()
                return None
            return await original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", racing_scalar)
        cart = await service._cart(user_id, create=True)
        assert cart is not None
        assert cart.user_id == user_id
    async with db_factory() as session:
        carts = (await session.scalars(select(Cart).where(Cart.user_id == user_id))).all()
    assert len(carts) == 1


@pytest.mark.asyncio
async def test_expiry_of_superseded_session_keeps_order_payable(
    client: AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_factory, "retry@example.com")
    headers = bearer(user_id)
    movie_id = await create_movie(db_factory, "Retry", "3.00")
    await client.post(f"/api/v1/cart/items/{movie_id}", headers=headers)
    order = (await client.post("/api/v1/orders", headers=headers)).json()["order"]
    # The first session has expired by the time the customer tries again.
    monkeypatch.setattr(
        stripe.checkout.Session,
        "retrieve",
        lambda session_id: SimpleNamespace(id=session_id, url=None, status="expired"),
    )
    for session_id in ("cs_old", "cs_new"):
        monkeypatch.setattr(
            stripe.checkout.Session,
            "create",
            lambda session_id=session_id, **_: SimpleNamespace(
                id=session_id, url="https://checkout.test"
            ),
        )
        checkout = await client.post(f"/api/v1/payments/checkout/{order['id']}", headers=headers)
        assert checkout.json()["checkout_id"] == session_id

    expired, expired_sig = signed_webhook(
        checkout_event(
            "evt_old_expired",
            "cs_old",
            order["id"],
            user_id,
            300,
            event_type="checkout.session.expired",
            payment_status="unpaid",
        )
    )
    response = await client.post("/api/v1/payments/webhook", content=expired, headers=expired_sig)
    assert response.json()["ignored"] is True
    detail = await client.get(f"/api/v1/orders/{order['id']}", headers=headers)
    assert detail.json()["status"] == "pending"

    paid, paid_sig = signed_webhook(
        checkout_event("evt_new_paid", "cs_new", order["id"], user_id, 300)
    )
    response = await client.post("/api/v1/payments/webhook", content=paid, headers=paid_sig)
    assert response.json()["status"] == "successful"
    detail = await client.get(f"/api/v1/orders/{order['id']}", headers=headers)
    assert detail.json()["status"] == "paid"


@pytest.mark.asyncio
async def test_charge_after_recorded_cancellation_upgrades_payment(
    client: AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_factory, "async@example.com")
    headers = bearer(user_id)
    movie_id = await create_movie(db_factory, "Async", "4.00")
    await client.post(f"/api/v1/cart/items/{movie_id}", headers=headers)
    order = (await client.post("/api/v1/orders", headers=headers)).json()["order"]
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **_: SimpleNamespace(id="cs_async", url="https://checkout.test"),
    )
    await client.post(f"/api/v1/payments/checkout/{order['id']}", headers=headers)
    failed, failed_sig = signed_webhook(
        checkout_event(
            "evt_async_failed",
            "cs_async",
            order["id"],
            user_id,
            400,
            event_type="checkout.session.async_payment_failed",
            payment_status="unpaid",
        )
    )
    response = await client.post("/api/v1/payments/webhook", content=failed, headers=failed_sig)
    assert response.json()["status"] == "canceled"

    paid, paid_sig = signed_webhook(
        checkout_event("evt_async_paid", "cs_async", order["id"], user_id, 400)
    )
    response = await client.post("/api/v1/payments/webhook", content=paid, headers=paid_sig)
    assert response.json()["status"] == "successful"
    [payment] = (await client.get("/api/v1/payments", headers=headers)).json()
    assert payment["status"] == "successful"
    detail = await client.get(f"/api/v1/orders/{order['id']}", headers=headers)
    assert detail.json()["status"] == "paid"


async def test_checkout_reuses_open_session(
    client: AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(db_factory, "reuse@example.com")
    headers = bearer(user_id)
    movie_id = await create_movie(db_factory, "Reuse", "5.00")
    await client.post(f"/api/v1/cart/items/{movie_id}", headers=headers)
    order = (await client.post("/api/v1/orders", headers=headers)).json()["order"]
    created: list[str] = []

    def create(**kwargs: object) -> SimpleNamespace:
        created.append(str(kwargs["idempotency_key"]))
        return SimpleNamespace(id="cs_open", url="https://checkout.test/open")

    monkeypatch.setattr(stripe.checkout.Session, "create", create)
    monkeypatch.setattr(
        stripe.checkout.Session,
        "retrieve",
        lambda session_id: SimpleNamespace(
            id=session_id, url="https://checkout.test/open", status="open"
        ),
    )
    first = await client.post(f"/api/v1/payments/checkout/{order['id']}", headers=headers)
    second = await client.post(f"/api/v1/payments/checkout/{order['id']}", headers=headers)
    assert first.json() == second.json()
    assert created == [f"checkout:{order['id']}:first"]
