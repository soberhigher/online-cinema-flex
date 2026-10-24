from collections.abc import AsyncGenerator

from httpx import AsyncClient
from sqlalchemy.exc import OperationalError

from app.core.database import get_session
from app.main import app


class BrokenSession:
    async def execute(self, *args: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


async def broken_session() -> AsyncGenerator[BrokenSession, None]:
    yield BrokenSession()


async def test_health_checks_database(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_reports_unavailable_database(client: AsyncClient) -> None:
    app.dependency_overrides[get_session] = broken_session
    response = await client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}
