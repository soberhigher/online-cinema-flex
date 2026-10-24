import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from uuid import UUID

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app
from app.models.accounts import User, UserGroup, UserGroupEnum
from app.models.payments import Payment
from app.models.purchases import Cart, CartItem
from app.tasks.auth_tasks import send_email

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://cinema:changeme@localhost:5432/cinema_test",
)
queued_emails: list[tuple[str, str, str]] = []
test_session_factory: async_sessionmaker | None = None


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as connection:
        await connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        await connection.exec_driver_sql("CREATE SCHEMA public")
    await engine.dispose()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    await asyncio.to_thread(command.upgrade, config, "head")
    global test_session_factory
    engine = create_async_engine(TEST_DATABASE_URL)
    test_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    factory = test_session_factory

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    queued_emails.clear()
    original_delay = send_email.delay
    send_email.delay = lambda to, subject, body: queued_emails.append((to, subject, body))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            yield test_client
    finally:
        send_email.delay = original_delay
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_registration_activation_refresh_and_logout(client: AsyncClient) -> None:
    email = "auth-flow@example.com"
    password = "StrongPassword9!"
    registered = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert registered.status_code == 201
    unactivated = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert unactivated.status_code == 403
    resend_email = "resend@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": resend_email, "password": password},
    )
    first_activation = re.search(r"token=([A-Za-z0-9_-]+)", queued_emails[-1][2])
    assert first_activation is not None
    await client.post("/api/v1/auth/resend-activation", json={"email": resend_email})
    second_activation = re.search(r"token=([A-Za-z0-9_-]+)", queued_emails[-1][2])
    assert second_activation is not None
    assert first_activation.group(1) != second_activation.group(1)
    assert (
        await client.get(
            "/api/v1/auth/activate",
            params={"token": first_activation.group(1)},
        )
    ).status_code == 400
    assert (
        await client.get(
            "/api/v1/auth/activate",
            params={"token": second_activation.group(1)},
        )
    ).status_code == 200

    primary_email, _, primary_body = queued_emails[0]
    assert primary_email == email
    activation_match = re.search(r"token=([A-Za-z0-9_-]+)", primary_body)
    assert activation_match is not None
    activation_token = activation_match.group(1)
    assert (
        await client.get("/api/v1/auth/activate", params={"token": activation_token})
    ).status_code == 200
    assert (
        await client.get("/api/v1/auth/activate", params={"token": activation_token})
    ).status_code == 400

    logged_in = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert logged_in.status_code == 200
    refresh_token = logged_in.json()["refresh_token"]
    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    assert refreshed.json()["token_type"] == "bearer"
    logged_out = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
    assert logged_out.status_code == 200
    rejected_refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert rejected_refresh.status_code == 401
    forgotten = await client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert forgotten.status_code == 200
    reset_match = re.search(r"Reset token: ([A-Za-z0-9_-]+)", queued_emails[-1][2])
    assert reset_match is not None
    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_match.group(1), "new_password": "ChangedPassword9!"},
    )
    assert reset.status_code == 200
    assert (
        await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "ChangedPassword9!"}
        )
    ).status_code == 200


def test_auth_routes_are_registered_in_openapi() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/auth/register",
        "/api/v1/auth/activate",
        "/api/v1/auth/resend-activation",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/change-password",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
    }
    assert expected <= set(paths)


@pytest.mark.asyncio
async def test_catalog_crud_filters_and_favorites(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stripe

    assert test_session_factory is not None
    async with test_session_factory() as session:
        group = await session.scalar(
            select(UserGroup).where(UserGroup.name == UserGroupEnum.MODERATOR)
        )
        assert group is not None
        moderator = User(
            email="catalog-mod@example.com",
            password_hash="unused",
            group_id=group.id,
            is_active=True,
        )
        session.add(moderator)
        await session.commit()
        user_id = moderator.id
    access_token, _ = create_access_token(str(user_id), UserGroupEnum.MODERATOR.value)
    headers = {"Authorization": f"Bearer {access_token}"}
    assert (
        await client.post("/api/v1/catalog-admin/genres", json={"name": "Drama"})
    ).status_code == 401
    created_genre = await client.post(
        "/api/v1/catalog-admin/genres", json={"name": "Drama"}, headers=headers
    )
    assert created_genre.status_code == 201
    genre_id = created_genre.json()["id"]
    movie_data = {
        "name": "Example Film",
        "year": 2024,
        "time": 120,
        "imdb": 8.4,
        "votes": 10,
        "price": "9.99",
        "genre_ids": [genre_id],
        "star_ids": [],
        "director_ids": [],
    }
    created_movie = await client.post(
        "/api/v1/catalog-admin/movies", json=movie_data, headers=headers
    )
    assert created_movie.status_code == 201
    movie_id = created_movie.json()["id"]
    duplicate = await client.post("/api/v1/catalog-admin/movies", json=movie_data, headers=headers)
    assert duplicate.status_code == 409
    filtered = await client.get(
        "/api/v1/movies/",
        params={"genre_id": genre_id, "year": 2024, "min_imdb": 8, "sort": "price"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["id"] == movie_id
    assert (await client.get(f"/api/v1/movies/{movie_id}")).status_code == 200
    assert (await client.post(f"/api/v1/favorites/{movie_id}", headers=headers)).status_code == 201
    assert (await client.post(f"/api/v1/favorites/{movie_id}", headers=headers)).status_code == 409
    favorite_list = await client.get("/api/v1/favorites", headers=headers)
    assert favorite_list.status_code == 200
    assert favorite_list.json()["total"] == 1
    assert (
        await client.delete(f"/api/v1/favorites/{movie_id}", headers=headers)
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/favorites/{movie_id}", headers=headers)
    ).status_code == 404
    assert (await client.get("/api/v1/cart", headers=headers)).json()["total"] == "0.00"
    assert (await client.post(f"/api/v1/cart/items/{movie_id}", headers=headers)).status_code == 201
    assert (await client.post(f"/api/v1/cart/items/{movie_id}", headers=headers)).status_code == 409
    blocked_delete = await client.delete(
        f"/api/v1/catalog-admin/movies/{movie_id}", headers=headers
    )
    assert blocked_delete.status_code == 409
    cart = await client.get("/api/v1/cart", headers=headers)
    assert cart.status_code == 200
    assert cart.json()["items"][0]["title"] == "Example Film"
    assert cart.json()["total"] == "9.99"
    order_response = await client.post("/api/v1/orders", headers=headers)
    assert order_response.status_code == 201
    order_data = order_response.json()["order"]
    assert order_data["status"] == "pending"
    assert order_data["total_amount"] == "9.99"
    assert order_data["items"][0]["price_at_order"] == "9.99"
    assert (await client.get("/api/v1/cart", headers=headers)).json()["items"] == []
    assert (await client.post(f"/api/v1/cart/items/{movie_id}", headers=headers)).status_code == 409
    checkout_ids = iter(["cs_test_example", "cs_test_canceled"])
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **kwargs: SimpleNamespace(
            id=next(checkout_ids), url="https://checkout.test/session"
        ),
    )
    checkout = await client.post(f"/api/v1/payments/checkout/{order_data['id']}", headers=headers)
    assert checkout.status_code == 200
    assert checkout.json()["checkout_id"] == "cs_test_example"

    event = {
        "id": "evt_test_success",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_example",
                "amount_total": 999,
                "payment_status": "paid",
                "metadata": {"order_id": order_data["id"], "user_id": str(user_id)},
            }
        },
    }
    payload = json.dumps(event, separators=(",", ":")).encode()
    timestamp = int(time.time())
    signed_payload = str(timestamp).encode() + b"." + payload
    signature = hmac.new(
        get_settings().stripe_webhook_secret.encode(), signed_payload, hashlib.sha256
    ).hexdigest()
    signature_header = f"t={timestamp},v1={signature}"
    rejected_webhook = await client.post(
        "/api/v1/payments/webhook",
        content=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1=invalid"},
    )
    assert rejected_webhook.status_code == 400
    webhook = await client.post(
        "/api/v1/payments/webhook",
        content=payload,
        headers={"Stripe-Signature": signature_header},
    )
    assert webhook.status_code == 200
    assert webhook.json()["status"] == "successful"
    repeated = await client.post(
        "/api/v1/payments/webhook",
        content=payload,
        headers={"Stripe-Signature": signature_header},
    )
    assert repeated.status_code == 200
    assert repeated.json()["duplicate"] is True
    assert (
        await client.post(f"/api/v1/orders/{order_data['id']}/cancel", headers=headers)
    ).status_code == 409
    async with test_session_factory() as session:
        payment_count = await session.scalar(select(func.count()).select_from(Payment))
    assert payment_count == 1
    second_movie_data = dict(movie_data, name="Canceled Example", year=2023)
    second_movie = await client.post(
        "/api/v1/catalog-admin/movies", json=second_movie_data, headers=headers
    )
    assert second_movie.status_code == 201
    second_movie_id = second_movie.json()["id"]
    assert (
        await client.post(f"/api/v1/cart/items/{second_movie_id}", headers=headers)
    ).status_code == 201
    second_order = await client.post("/api/v1/orders", headers=headers)
    assert second_order.status_code == 201
    second_order_data = second_order.json()["order"]
    second_checkout = await client.post(
        f"/api/v1/payments/checkout/{second_order_data['id']}", headers=headers
    )
    assert second_checkout.status_code == 200
    canceled_event = {
        "id": "evt_test_canceled",
        "type": "checkout.session.expired",
        "data": {
            "object": {
                "id": "cs_test_canceled",
                "metadata": {"order_id": second_order_data["id"], "user_id": str(user_id)},
            }
        },
    }
    canceled_payload = json.dumps(canceled_event, separators=(",", ":")).encode()
    canceled_timestamp = int(time.time())
    canceled_signed = str(canceled_timestamp).encode() + b"." + canceled_payload
    canceled_signature = hmac.new(
        get_settings().stripe_webhook_secret.encode(), canceled_signed, hashlib.sha256
    ).hexdigest()
    canceled_response = await client.post(
        "/api/v1/payments/webhook",
        content=canceled_payload,
        headers={"Stripe-Signature": f"t={canceled_timestamp},v1={canceled_signature}"},
    )
    assert canceled_response.status_code == 200
    assert canceled_response.json()["status"] == "canceled"
    canceled_order = await client.get(f"/api/v1/orders/{second_order_data['id']}", headers=headers)
    assert canceled_order.json()["status"] == "canceled"
    async with test_session_factory() as session:
        cart_row = await session.scalar(select(Cart).where(Cart.user_id == user_id))
        assert cart_row is not None
        session.add(CartItem(cart_id=cart_row.id, movie_id=UUID(movie_id)))
        await session.commit()
    stale_order = await client.post("/api/v1/orders", headers=headers)
    assert stale_order.status_code == 409
    assert stale_order.json()["detail"]["excluded"] == [
        {"movie_id": movie_id, "reason": "purchased_or_pending"}
    ]
