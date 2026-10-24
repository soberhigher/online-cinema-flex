import asyncio
import os
import re
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_session
from app.main import app
from app.tasks.auth_tasks import send_email

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://cinema:changeme@localhost:5432/cinema_test",
)
queued_emails: list[tuple[str, str, str]] = []


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
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

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
