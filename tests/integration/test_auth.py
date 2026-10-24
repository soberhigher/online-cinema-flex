import re
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.accounts import ActivationToken, PasswordResetToken, User
from tests.helpers import queued_emails


@pytest.mark.asyncio
async def test_registration_activation_refresh_and_logout(
    client: AsyncClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    email = "auth-flow@example.com"
    password = "StrongPassword9!"
    malformed_refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "not-a-jwt"}
    )
    assert malformed_refresh.status_code == 401
    malformed_logout = await client.post("/api/v1/auth/logout", json={"refresh_token": "not-a-jwt"})
    assert malformed_logout.status_code == 401
    malformed_access = await client.get(
        "/api/v1/favorites", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert malformed_access.status_code == 401
    registered = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert registered.status_code == 201
    assert (
        await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    ).status_code == 409
    assert (
        await client.post("/api/v1/auth/resend-activation", json={"email": "unknown@example.com"})
    ).status_code == 200
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
    async with db_factory() as session:
        resend_user = await session.scalar(select(User).where(User.email == resend_email))
        assert resend_user is not None
        active_token = await session.get(ActivationToken, resend_user.id)
        assert active_token is not None
        active_token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert (
        await client.get(
            "/api/v1/auth/activate",
            params={"token": second_activation.group(1)},
        )
    ).status_code == 400
    await client.post("/api/v1/auth/resend-activation", json={"email": resend_email})
    third_activation = re.search(r"token=([A-Za-z0-9_-]+)", queued_emails[-1][2])
    assert third_activation is not None
    assert third_activation.group(1) != second_activation.group(1)
    assert (
        await client.get(
            "/api/v1/auth/activate",
            params={"token": third_activation.group(1)},
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
    wrong_password = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "WrongPassword9!"}
    )
    assert wrong_password.status_code == 401

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
    async with db_factory() as session:
        reset_user = await session.scalar(select(User).where(User.email == email))
        assert reset_user is not None
        reset_row = await session.get(PasswordResetToken, reset_user.id)
        assert reset_row is not None
        reset_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    expired_reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_match.group(1), "new_password": "ChangedPassword9!"},
    )
    assert expired_reset.status_code == 400
    await client.post("/api/v1/auth/forgot-password", json={"email": email})
    first_unexpired_reset = re.search(r"Reset token: ([A-Za-z0-9_-]+)", queued_emails[-1][2])
    assert first_unexpired_reset is not None
    await client.post("/api/v1/auth/forgot-password", json={"email": email})
    reset_match = re.search(r"Reset token: ([A-Za-z0-9_-]+)", queued_emails[-1][2])
    assert reset_match is not None
    assert reset_match.group(1) != first_unexpired_reset.group(1)
    replaced_reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": first_unexpired_reset.group(1), "new_password": "ChangedPassword9!"},
    )
    assert replaced_reset.status_code == 400
    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_match.group(1), "new_password": "ChangedPassword9!"},
    )
    assert reset.status_code == 200
    assert (
        await client.post(
            "/api/v1/auth/reset-password",
            json={"token": reset_match.group(1), "new_password": "AfterReset9!"},
        )
    ).status_code == 400
    assert (
        await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    ).status_code == 401
    changed_login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "ChangedPassword9!"}
    )
    assert changed_login.status_code == 200
    access = changed_login.json()["access_token"]
    wrong_old_password = await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Incorrect9!", "new_password": "AfterChange9!"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert wrong_old_password.status_code == 400
    change = await client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "ChangedPassword9!", "new_password": "AfterChange9!"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert change.status_code == 200
    revoked = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": changed_login.json()["refresh_token"]},
    )
    assert revoked.status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "ChangedPassword9!"}
        )
    ).status_code == 401
    assert (
        await client.post("/api/v1/auth/login", json={"email": email, "password": "AfterChange9!"})
    ).status_code == 200


@pytest.mark.asyncio
async def test_login_errors_and_mail_outage_do_not_break_registration(
    client: AsyncClient,
) -> None:
    # A short wrong password is a credentials error, not a policy violation.
    short = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "short"}
    )
    assert short.status_code == 401

    from app.tasks.auth_tasks import send_email

    original = send_email.delay

    def broken_delay(*args: object) -> None:
        raise RuntimeError("broker down")

    send_email.delay = broken_delay
    try:
        registered = await client.post(
            "/api/v1/auth/register",
            json={"email": "outage@example.com", "password": "StrongPassword9!"},
        )
    finally:
        send_email.delay = original
    # The account is stored even though the email could not be queued...
    assert registered.status_code == 201
    # ...and the user can ask for a new activation link.
    resent = await client.post(
        "/api/v1/auth/resend-activation", json={"email": "outage@example.com"}
    )
    assert resent.status_code == 200
    assert queued_emails[-1][0] == "outage@example.com"
    assert "/api/v1/auth/activate?token=" in queued_emails[-1][2]
