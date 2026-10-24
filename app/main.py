from fastapi import FastAPI

from app.api.routes import health
from app.core.config import get_settings

settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Online Cinema FLEX",
        version="0.1.0",
        description=(
            "FLEX backend: accounts & auth, movie catalog, favorites, cart, orders, "
            "and Stripe test payments."
        ),
        docs_url=None,  # documentation wiring lands with the auth feature branch
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(health.router)
    return app


app = create_app()
