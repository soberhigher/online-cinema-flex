import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", summary="Health check")
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Readiness probe. Returns 503 when the database is unreachable."""
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        logger.exception("Health check: database unavailable")
        return JSONResponse({"status": "error", "database": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})
