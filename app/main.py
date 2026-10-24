from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

from app.api.dependencies import get_current_user
from app.api.routes import auth, catalog, favorites, health
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
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(catalog.router)
    app.include_router(catalog.admin)
    app.include_router(favorites.router)

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi(user: Annotated[Any, Depends(get_current_user)]) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    async def docs(user: Annotated[Any, Depends(get_current_user)]) -> Any:
        return get_swagger_ui_html(openapi_url="/openapi.json", title="Online Cinema FLEX API")

    return app


app = create_app()
