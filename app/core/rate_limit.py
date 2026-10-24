import time
from collections import defaultdict, deque
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request


class RateLimiter:
    """Sliding-window counter kept in process memory.

    Good enough for the single Uvicorn process started by docker-compose; several workers
    would each count separately and need a shared store such as Redis instead.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window: float) -> float | None:
        """Record a hit; return seconds to wait if the limit is exceeded, else None."""
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return hits[0] + window - now
        hits.append(now)
        return None

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()


def check_rate_limit(request: Request, scope: str, limit: int, window: float) -> None:
    client = request.client.host if request.client else "unknown"
    retry_after = limiter.hit(f"{scope}:{client}", limit, window)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many requests, try again later",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


def rate_limit(
    scope: str, limit: int, window: float = 60
) -> Callable[[Request], Coroutine[Any, Any, None]]:
    async def dependency(request: Request) -> None:
        check_rate_limit(request, scope, limit, window)

    return dependency
