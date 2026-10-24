from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Liveness/readiness probe. Verifies the database connection."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - direct DB failure path
        raise RuntimeError(f"Database check failed: {exc}") from exc
    return {"status": "ok", "database": "ok"}
