import base64

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import STRONG_PASSWORD, bearer, create_user


def basic(email: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{email}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.asyncio
async def test_docs_accept_basic_and_bearer_but_not_anonymous(
    client: AsyncClient, db_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await create_user(db_factory, "docs@example.com", password=STRONG_PASSWORD)
    anonymous = await client.get("/docs")
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"].startswith("Basic")
    assert (
        await client.get("/docs", headers=basic("docs@example.com", "Wrong9!"))
    ).status_code == 401
    browser = await client.get("/docs", headers=basic("docs@example.com", STRONG_PASSWORD))
    assert browser.status_code == 200
    assert "SwaggerUIBundle" in browser.text
    assert (await client.get("/openapi.json", headers=bearer(user_id))).status_code == 200
