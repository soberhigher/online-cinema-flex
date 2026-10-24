import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import build_parser, seed_catalog
from app.cli import create_user as cli_create_user
from app.models.accounts import UserGroupEnum
from app.models.catalog import Movie
from tests.helpers import STRONG_PASSWORD, bearer, create_user


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserGroupEnum.USER, UserGroupEnum.MODERATOR])
async def test_non_admins_are_forbidden(
    client: AsyncClient, db_factory: async_sessionmaker[AsyncSession], role: UserGroupEnum
) -> None:
    user_id = await create_user(db_factory, "someone@example.com", role)
    headers = bearer(user_id, role)
    assert (await client.get("/api/v1/admin/users", headers=headers)).status_code == 403
    response = await client.patch(
        f"/api/v1/admin/users/{user_id}/group", json={"group": "ADMIN"}, headers=headers
    )
    assert response.status_code == 403
    response = await client.post(f"/api/v1/admin/users/{user_id}/activate", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_lists_changes_group_and_activates(
    client: AsyncClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    admin_id = await create_user(db_factory, "admin@example.com", UserGroupEnum.ADMIN)
    headers = bearer(admin_id, UserGroupEnum.ADMIN)
    user_id = await create_user(
        db_factory, "pending@example.com", password=STRONG_PASSWORD, active=False
    )

    listing = await client.get("/api/v1/admin/users", params={"email": "PENDING"}, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["is_active"] is False

    page = await client.get("/api/v1/admin/users", params={"per_page": 1}, headers=headers)
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1

    changed = await client.patch(
        f"/api/v1/admin/users/{user_id}/group", json={"group": "MODERATOR"}, headers=headers
    )
    assert changed.status_code == 200
    assert changed.json()["group"] == "MODERATOR"
    bad = await client.patch(
        f"/api/v1/admin/users/{user_id}/group", json={"group": "ROOT"}, headers=headers
    )
    assert bad.status_code == 422

    login = {"email": "pending@example.com", "password": STRONG_PASSWORD}
    assert (await client.post("/api/v1/auth/login", json=login)).status_code in {401, 403}
    activated = await client.post(f"/api/v1/admin/users/{user_id}/activate", headers=headers)
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True
    assert (await client.post("/api/v1/auth/login", json=login)).status_code == 200

    missing = "00000000-0000-0000-0000-000000000000"
    response = await client.post(f"/api/v1/admin/users/{missing}/activate", headers=headers)
    assert response.status_code == 404
    response = await client.patch(
        f"/api/v1/admin/users/{missing}/group", json={"group": "USER"}, headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cli_creates_admin_and_seeds_catalog(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_factory() as session:
        message = await cli_create_user(session, "Root@Example.com", STRONG_PASSWORD, "ADMIN")
        assert "root@example.com" in message
        with pytest.raises(SystemExit):
            await cli_create_user(session, "root@example.com", STRONG_PASSWORD, "ADMIN")
        first = await seed_catalog(session)
        second = await seed_catalog(session)
        movies = (await session.scalars(select(Movie))).all()
    assert first.startswith("Seeded 7")
    assert second.startswith("Seeded 0")
    assert len(movies) == 7


def test_cli_parser() -> None:
    args = build_parser().parse_args(
        ["create-user", "--email", "a@example.com", "--password", "x", "--role", "ADMIN"]
    )
    assert (args.command, args.role) == ("create-user", "ADMIN")
    assert build_parser().parse_args(["seed-catalog"]).command == "seed-catalog"
