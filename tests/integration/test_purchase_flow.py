"""Long end-to-end scenario: catalog administration through Stripe payment."""

import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.accounts import User, UserGroup, UserGroupEnum
from app.models.catalog import Movie
from app.models.payments import Payment
from app.models.purchases import Cart, CartItem
from app.tasks.auth_tasks import send_email


@pytest.mark.asyncio
async def test_catalog_cart_order_and_payment_flow(
    client: AsyncClient,
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stripe

    async with db_factory() as session:
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
    assert (await client.post("/api/v1/orders", headers=headers)).status_code == 409
    async with db_factory() as session:
        user_group = await session.scalar(
            select(UserGroup).where(UserGroup.name == UserGroupEnum.USER)
        )
        assert user_group is not None
        regular_user = User(
            email="catalog-user@example.com",
            password_hash="unused",
            group_id=user_group.id,
            is_active=True,
        )
        session.add(regular_user)
        await session.commit()
        regular_user_id = regular_user.id
    regular_token, _ = create_access_token(str(regular_user_id), UserGroupEnum.USER.value)
    regular_headers = {"Authorization": f"Bearer {regular_token}"}
    denied_admin = await client.post(
        "/api/v1/catalog-admin/genres",
        json={"name": "Forbidden Genre"},
        headers=regular_headers,
    )
    assert denied_admin.status_code == 403
    async with db_factory() as session:
        admin_group = await session.scalar(
            select(UserGroup).where(UserGroup.name == UserGroupEnum.ADMIN)
        )
        assert admin_group is not None
        admin_user = User(
            email="catalog-admin@example.com",
            password_hash="unused",
            group_id=admin_group.id,
            is_active=True,
        )
        session.add(admin_user)
        await session.commit()
        admin_id = admin_user.id
    admin_token, _ = create_access_token(str(admin_id), UserGroupEnum.ADMIN.value)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    assert (
        await client.post(
            "/api/v1/catalog-admin/genres",
            json={"name": "Admin Managed"},
            headers=admin_headers,
        )
    ).status_code == 201
    assert (await client.get("/docs")).status_code == 401
    assert (await client.get("/openapi.json")).status_code == 401
    docs = await client.get("/docs", headers=headers)
    assert docs.status_code == 200
    assert "SwaggerUIBundle" in docs.text
    assert (await client.get("/openapi.json", headers=headers)).status_code == 200
    openapi_schema = (await client.get("/openapi.json", headers=headers)).json()
    oauth_scheme = openapi_schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]
    assert oauth_scheme["flows"]["password"]["tokenUrl"] == "/api/v1/auth/token"
    assert (
        await client.post("/api/v1/catalog-admin/genres", json={"name": "Drama"})
    ).status_code == 401
    created_genre = await client.post(
        "/api/v1/catalog-admin/genres", json={"name": "Drama"}, headers=headers
    )
    assert created_genre.status_code == 201
    assert (
        await client.post("/api/v1/catalog-admin/genres", json={"name": "Drama"}, headers=headers)
    ).status_code == 409
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
    lead_star = await client.post(
        "/api/v1/catalog-admin/stars", json={"name": "Lead Actor"}, headers=headers
    )
    lead_director = await client.post(
        "/api/v1/catalog-admin/directors", json={"name": "Lead Director"}, headers=headers
    )
    assert lead_star.status_code == 201
    assert lead_director.status_code == 201
    movie_data["star_ids"] = [lead_star.json()["id"]]
    movie_data["director_ids"] = [lead_director.json()["id"]]
    created_movie = await client.post(
        "/api/v1/catalog-admin/movies", json=movie_data, headers=headers
    )
    assert created_movie.status_code == 201
    movie_id = created_movie.json()["id"]
    genre_movie_list = await client.get(f"/api/v1/movies/genres/{genre_id}/movies")
    assert genre_movie_list.status_code == 200
    assert genre_movie_list.json()[0]["id"] == movie_id
    assert "genres" in genre_movie_list.json()[0]
    unused_genre = await client.post(
        "/api/v1/catalog-admin/genres", json={"name": "Unused"}, headers=headers
    )
    assert unused_genre.status_code == 201
    temporary_genre = await client.post(
        "/api/v1/catalog-admin/genres", json={"name": "Temporary"}, headers=headers
    )
    temporary_genre_id = temporary_genre.json()["id"]
    assert (await client.get("/api/v1/catalog-admin/genres", headers=headers)).status_code == 200
    renamed_genre = await client.put(
        f"/api/v1/catalog-admin/genres/{temporary_genre_id}",
        json={"name": "Temporary Renamed"},
        headers=headers,
    )
    assert renamed_genre.status_code == 200
    assert renamed_genre.json()["name"] == "Temporary Renamed"
    assert (
        await client.delete(f"/api/v1/catalog-admin/genres/{temporary_genre_id}", headers=headers)
    ).status_code == 204
    for resource, name in (("stars", "Temporary Star"), ("directors", "Temporary Director")):
        created_reference = await client.post(
            f"/api/v1/catalog-admin/{resource}", json={"name": name}, headers=headers
        )
        assert created_reference.status_code == 201
        reference_id = created_reference.json()["id"]
        listed_references = await client.get(f"/api/v1/catalog-admin/{resource}", headers=headers)
        assert created_reference.json() in listed_references.json()
        updated_reference = await client.put(
            f"/api/v1/catalog-admin/{resource}/{reference_id}",
            json={"name": f"{name} Renamed"},
            headers=headers,
        )
        assert updated_reference.status_code == 200
        assert (
            await client.delete(f"/api/v1/catalog-admin/{resource}/{reference_id}", headers=headers)
        ).status_code == 204
    temporary_certification = await client.post(
        "/api/v1/catalog-admin/certifications",
        json={"name": "Temporary Cert"},
        headers=headers,
    )
    temp_cert_id = temporary_certification.json()["id"]
    listed_certifications = await client.get(
        "/api/v1/catalog-admin/certifications", headers=headers
    )
    assert temporary_certification.json() in listed_certifications.json()
    updated_certification = await client.put(
        f"/api/v1/catalog-admin/certifications/{temp_cert_id}",
        json={"name": "Temporary Cert Renamed"},
        headers=headers,
    )
    assert updated_certification.status_code == 200
    assert (
        await client.delete(f"/api/v1/catalog-admin/certifications/{temp_cert_id}", headers=headers)
    ).status_code == 204
    assert (
        await client.delete(
            f"/api/v1/catalog-admin/genres/{unused_genre.json()['id']}", headers=headers
        )
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/catalog-admin/genres/{genre_id}", headers=headers)
    ).status_code == 409
    assert (await client.post(f"/api/v1/favorites/{movie_id}")).status_code == 401
    duplicate = await client.post("/api/v1/catalog-admin/movies", json=movie_data, headers=headers)
    assert duplicate.status_code == 409
    comedy_genre = await client.post(
        "/api/v1/catalog-admin/genres", json={"name": "Comedy"}, headers=headers
    )
    second_star = await client.post(
        "/api/v1/catalog-admin/stars", json={"name": "Second Actor"}, headers=headers
    )
    second_director = await client.post(
        "/api/v1/catalog-admin/directors",
        json={"name": "Second Director"},
        headers=headers,
    )
    second_movie_data = dict(
        movie_data,
        name="Second Catalog Film",
        year=2022,
        price="12.50",
        genre_ids=[comedy_genre.json()["id"]],
        star_ids=[second_star.json()["id"]],
        director_ids=[second_director.json()["id"]],
    )
    second_catalog_movie = await client.post(
        "/api/v1/catalog-admin/movies", json=second_movie_data, headers=headers
    )
    assert second_catalog_movie.status_code == 201
    assert (await client.get("/api/v1/movies/", params={"search": "Second Director"})).json()[
        "total"
    ] == 1
    assert (await client.get("/api/v1/movies/", params={"search": "12.50"})).json()["total"] == 1
    wrong_genre_year = await client.get(
        "/api/v1/movies/",
        params={"genre_id": genre_id, "year": 2022, "min_imdb": 8},
    )
    assert wrong_genre_year.status_code == 200
    assert wrong_genre_year.json()["total"] == 0
    catalog_page_one = await client.get(
        "/api/v1/movies/",
        params={"page": 1, "per_page": 1, "sort": "price", "min_imdb": 8.4},
    )
    catalog_page_two = await client.get(
        "/api/v1/movies/",
        params={"page": 2, "per_page": 1, "sort": "price", "min_imdb": 8.4},
    )
    assert catalog_page_one.json()["total"] == 2
    assert catalog_page_one.json()["items"][0]["id"] == movie_id
    assert catalog_page_two.json()["items"][0]["id"] == second_catalog_movie.json()["id"]
    descending_page = await client.get(
        "/api/v1/movies/",
        params={"page": 1, "per_page": 1, "sort": "price", "descending": True, "min_imdb": 8.4},
    )
    assert descending_page.json()["items"][0]["id"] == second_catalog_movie.json()["id"]
    updated_data = dict(movie_data, description="Updated description")
    updated_movie = await client.put(
        f"/api/v1/catalog-admin/movies/{movie_id}", json=updated_data, headers=headers
    )
    assert updated_movie.status_code == 200
    assert updated_movie.json()["description"] == "Updated description"
    assert (await client.get("/api/v1/movies/", params={"search": "Updated description"})).json()[
        "total"
    ] == 1
    assert (await client.get("/api/v1/movies/", params={"search": "Lead Actor"})).json()[
        "total"
    ] == 1
    other_favorite = await client.get("/api/v1/favorites", headers=regular_headers)
    assert other_favorite.status_code == 200
    assert other_favorite.json()["total"] == 0
    assert (
        await client.post(f"/api/v1/favorites/{movie_id}", headers=regular_headers)
    ).status_code == 201
    other_favorite = await client.get("/api/v1/favorites", headers=regular_headers)
    assert other_favorite.json()["total"] == 1
    assert other_favorite.json()["items"][0]["id"] == movie_id
    assert (await client.get("/api/v1/cart", headers=regular_headers)).json()["total"] == "0.00"
    searched_genre = await client.get("/api/v1/movies/", params={"search": "Drama"})
    assert searched_genre.status_code == 200
    assert searched_genre.json()["total"] == 1
    searched_rating = await client.get("/api/v1/movies/", params={"search": "8.4"})
    assert searched_rating.status_code == 200
    assert searched_rating.json()["total"] == 2
    second_year = await client.get("/api/v1/movies/", params={"year": 2022})
    assert [row["id"] for row in second_year.json()["items"]] == [second_catalog_movie.json()["id"]]
    second_genre = await client.get(
        "/api/v1/movies/", params={"genre_id": comedy_genre.json()["id"]}
    )
    assert [row["id"] for row in second_genre.json()["items"]] == [
        second_catalog_movie.json()["id"]
    ]
    filtered = await client.get(
        "/api/v1/movies/",
        params={"genre_id": genre_id, "year": 2024, "min_imdb": 8, "sort": "price"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    empty = await client.get("/api/v1/movies/", params={"search": "no-such-title"})
    assert empty.status_code == 200
    assert empty.json()["total"] == 0
    assert empty.json()["items"] == []
    assert (await client.get("/api/v1/movies/", params={"sort": "invalid"})).status_code == 422
    assert (
        await client.post(
            "/api/v1/catalog-admin/movies",
            json=dict(
                movie_data,
                name="Missing Certification",
                year=2020,
                certification_id=99999,
            ),
            headers=headers,
        )
    ).status_code == 404
    certification = await client.post(
        "/api/v1/catalog-admin/certifications", json={"name": "PG-13"}, headers=headers
    )
    assert certification.status_code == 201
    certified_movie = await client.post(
        "/api/v1/catalog-admin/movies",
        json=dict(
            movie_data,
            name="Certified Example",
            year=2020,
            certification_id=certification.json()["id"],
        ),
        headers=headers,
    )
    assert certified_movie.status_code == 201
    assert certified_movie.json()["certification_id"] == certification.json()["id"]
    certification_rows = await client.get("/api/v1/catalog-admin/certifications", headers=headers)
    assert certification_rows.status_code == 200
    assert certification.json() in certification_rows.json()
    catalog_page_one = await client.get(
        "/api/v1/movies/", params={"page": 1, "per_page": 1, "sort": "price"}
    )
    catalog_page_two = await client.get(
        "/api/v1/movies/", params={"page": 2, "per_page": 1, "sort": "price"}
    )
    assert catalog_page_one.json()["total"] >= 2
    assert len(catalog_page_one.json()["items"]) == 1
    assert catalog_page_two.json()["page"] == 2
    assert catalog_page_one.json()["items"][0]["id"] != catalog_page_two.json()["items"][0]["id"]
    movie_detail = await client.get(f"/api/v1/movies/{movie_id}")
    assert movie_detail.status_code == 200
    assert movie_detail.json()["genres"] == [{"id": genre_id, "name": "Drama"}]
    assert (
        await client.delete(
            f"/api/v1/catalog-admin/certifications/{certification.json()['id']}",
            headers=headers,
        )
    ).status_code == 409
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
    assert (
        await client.post(f"/api/v1/orders/{order_data['id']}/cancel", headers=regular_headers)
    ).status_code == 404
    assert (await client.get("/api/v1/orders", headers=regular_headers)).json() == []
    assert (await client.post("/api/v1/orders", headers=headers)).status_code == 409
    checkout_ids = iter(["cs_test_example", "cs_test_canceled", "cs_test_delayed"])
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
    async with db_factory() as session:
        purchased_movie = await session.get(Movie, UUID(movie_id))
        assert purchased_movie is not None
        original_price = purchased_movie.price
        purchased_movie.price = Decimal("12.99")
        await session.commit()
    stale_checkout = await client.post(
        f"/api/v1/payments/checkout/{order_data['id']}", headers=headers
    )
    assert stale_checkout.status_code == 409
    async with db_factory() as session:
        purchased_movie = await session.get(Movie, UUID(movie_id))
        assert purchased_movie is not None
        purchased_movie.price = original_price
        await session.commit()

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
    wrong_owner_event = {
        "id": "evt_test_wrong_owner",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_example",
                "amount_total": 999,
                "payment_status": "paid",
                "metadata": {"order_id": order_data["id"], "user_id": str(regular_user_id)},
            }
        },
    }
    wrong_owner_payload = json.dumps(wrong_owner_event, separators=(",", ":")).encode()
    wrong_owner_timestamp = int(time.time())
    wrong_owner_signed = str(wrong_owner_timestamp).encode() + b"." + wrong_owner_payload
    wrong_owner_signature = hmac.new(
        get_settings().stripe_webhook_secret.encode(), wrong_owner_signed, hashlib.sha256
    ).hexdigest()
    wrong_owner = await client.post(
        "/api/v1/payments/webhook",
        content=wrong_owner_payload,
        headers={"Stripe-Signature": f"t={wrong_owner_timestamp},v1={wrong_owner_signature}"},
    )
    assert wrong_owner.status_code == 400
    wrong_amount_event = {
        "id": "evt_test_wrong_amount",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_example",
                "amount_total": 1,
                "payment_status": "paid",
                "metadata": {"order_id": order_data["id"], "user_id": str(user_id)},
            }
        },
    }
    wrong_amount_payload = json.dumps(wrong_amount_event, separators=(",", ":")).encode()
    wrong_amount_timestamp = int(time.time())
    wrong_amount_signed = str(wrong_amount_timestamp).encode() + b"." + wrong_amount_payload
    wrong_amount_signature = hmac.new(
        get_settings().stripe_webhook_secret.encode(), wrong_amount_signed, hashlib.sha256
    ).hexdigest()
    wrong_amount = await client.post(
        "/api/v1/payments/webhook",
        content=wrong_amount_payload,
        headers={"Stripe-Signature": f"t={wrong_amount_timestamp},v1={wrong_amount_signature}"},
    )
    assert wrong_amount.status_code == 400
    still_pending = await client.get(f"/api/v1/orders/{order_data['id']}", headers=headers)
    assert still_pending.json()["status"] == "pending"
    original_mail_delay = send_email.delay
    send_email.delay = lambda *args: (_ for _ in ()).throw(RuntimeError("mail queue unavailable"))
    webhook = await client.post(
        "/api/v1/payments/webhook",
        content=payload,
        headers={"Stripe-Signature": signature_header},
    )
    send_email.delay = original_mail_delay
    assert webhook.status_code == 200
    assert webhook.json()["status"] == "successful"
    paid_after_email_failure = await client.get(
        f"/api/v1/orders/{order_data['id']}", headers=headers
    )
    assert paid_after_email_failure.json()["status"] == "paid"
    paid_delete = await client.delete(f"/api/v1/catalog-admin/movies/{movie_id}", headers=headers)
    assert paid_delete.status_code == 409
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
    async with db_factory() as session:
        payment_count = await session.scalar(select(func.count()).select_from(Payment))
    assert payment_count == 1
    repeated_success_event = dict(event, id="evt_test_success_second_delivery")
    repeated_success_payload = json.dumps(repeated_success_event, separators=(",", ":")).encode()
    repeated_timestamp = int(time.time())
    repeated_signed = str(repeated_timestamp).encode() + b"." + repeated_success_payload
    repeated_signature = hmac.new(
        get_settings().stripe_webhook_secret.encode(), repeated_signed, hashlib.sha256
    ).hexdigest()
    repeated_different_event = await client.post(
        "/api/v1/payments/webhook",
        content=repeated_success_payload,
        headers={"Stripe-Signature": f"t={repeated_timestamp},v1={repeated_signature}"},
    )
    assert repeated_different_event.status_code == 200
    assert repeated_different_event.json()["duplicate"] is True
    assert (await client.get(f"/api/v1/orders/{order_data['id']}", headers=headers)).json()[
        "status"
    ] == "paid"
    async with db_factory() as session:
        order_payment_count = await session.scalar(
            select(func.count())
            .select_from(Payment)
            .where(Payment.order_id == UUID(order_data["id"]))
        )
    assert order_payment_count == 1
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
    assert len((await client.get("/api/v1/orders", headers=headers)).json()) == 2
    assert (
        await client.get(f"/api/v1/orders/{second_order_data['id']}", headers=regular_headers)
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/cart/items/{second_movie_id}", headers=headers)
    ).status_code == 201
    assert (
        await client.delete(f"/api/v1/cart/items/{second_movie_id}", headers=headers)
    ).status_code == 204
    assert (
        await client.delete(f"/api/v1/cart/items/{second_movie_id}", headers=headers)
    ).status_code == 404
    await client.post(f"/api/v1/cart/items/{second_movie_id}", headers=headers)
    assert (await client.delete("/api/v1/cart", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/cart", headers=headers)).json()["items"] == []
    await client.post(f"/api/v1/cart/items/{second_movie_id}", headers=headers)
    pending_order = await client.post("/api/v1/orders", headers=headers)
    assert pending_order.status_code == 201
    pending_id = pending_order.json()["order"]["id"]
    pending_cancel = await client.post(f"/api/v1/orders/{pending_id}/cancel", headers=headers)
    assert pending_cancel.status_code == 200
    assert pending_cancel.json()["status"] == "canceled"
    assert (
        await client.post(f"/api/v1/orders/{pending_id}/cancel", headers=headers)
    ).status_code == 409
    delayed_movie = await client.post(
        "/api/v1/catalog-admin/movies",
        json=dict(movie_data, name="Delayed Payment", year=2022),
        headers=headers,
    )
    assert delayed_movie.status_code == 201
    delayed_movie_id = delayed_movie.json()["id"]
    await client.post(f"/api/v1/cart/items/{delayed_movie_id}", headers=headers)
    delayed_order = await client.post("/api/v1/orders", headers=headers)
    delayed_order_data = delayed_order.json()["order"]
    delayed_checkout = await client.post(
        f"/api/v1/payments/checkout/{delayed_order_data['id']}", headers=headers
    )
    assert delayed_checkout.status_code == 200
    delayed_metadata = {"order_id": delayed_order_data["id"], "user_id": str(user_id)}
    unpaid_event = {
        "id": "evt_test_delayed_unpaid",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_delayed",
                "amount_total": 999,
                "payment_status": "unpaid",
                "metadata": delayed_metadata,
            }
        },
    }
    unpaid_payload = json.dumps(unpaid_event, separators=(",", ":")).encode()
    unpaid_timestamp = int(time.time())
    unpaid_signed = str(unpaid_timestamp).encode() + b"." + unpaid_payload
    unpaid_signature = hmac.new(
        get_settings().stripe_webhook_secret.encode(), unpaid_signed, hashlib.sha256
    ).hexdigest()
    unpaid_response = await client.post(
        "/api/v1/payments/webhook",
        content=unpaid_payload,
        headers={"Stripe-Signature": f"t={unpaid_timestamp},v1={unpaid_signature}"},
    )
    assert unpaid_response.status_code == 200
    assert unpaid_response.json()["pending"] is True
    unpaid_order = await client.get(f"/api/v1/orders/{delayed_order_data['id']}", headers=headers)
    assert unpaid_order.json()["status"] == "pending"
    success_event = {
        "id": "evt_test_delayed_success",
        "type": "checkout.session.async_payment_succeeded",
        "data": {
            "object": {
                "id": "cs_test_delayed",
                "amount_total": 999,
                "payment_status": "paid",
                "metadata": delayed_metadata,
            }
        },
    }
    success_payload = json.dumps(success_event, separators=(",", ":")).encode()
    success_timestamp = int(time.time())
    success_signed = str(success_timestamp).encode() + b"." + success_payload
    success_signature = hmac.new(
        get_settings().stripe_webhook_secret.encode(), success_signed, hashlib.sha256
    ).hexdigest()
    success_response = await client.post(
        "/api/v1/payments/webhook",
        content=success_payload,
        headers={"Stripe-Signature": f"t={success_timestamp},v1={success_signature}"},
    )
    assert success_response.status_code == 200
    assert success_response.json()["status"] == "successful"
    race_movie = await client.post(
        "/api/v1/catalog-admin/movies",
        json=dict(movie_data, name="Order Race", year=2021),
        headers=headers,
    )
    race_movie_id = race_movie.json()["id"]
    await client.post(f"/api/v1/cart/items/{race_movie_id}", headers=headers)
    concurrent_orders = await asyncio.gather(
        client.post("/api/v1/orders", headers=headers),
        client.post("/api/v1/orders", headers=headers),
    )
    assert sorted(response.status_code for response in concurrent_orders) == [201, 409]
    async with db_factory() as session:
        cart_row = await session.scalar(select(Cart).where(Cart.user_id == user_id))
        assert cart_row is not None
        session.add(CartItem(cart_id=cart_row.id, movie_id=UUID(movie_id)))
        await session.commit()
    stale_order = await client.post("/api/v1/orders", headers=headers)
    assert stale_order.status_code == 409
    assert stale_order.json()["detail"]["excluded"] == [
        {"movie_id": movie_id, "reason": "purchased_or_pending"}
    ]
