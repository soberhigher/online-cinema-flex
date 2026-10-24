import json
import logging
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.dependencies import get_docs_user
from app.api.routes import admin, auth, catalog, favorites, health, payments, purchases
from app.core.config import get_settings
from app.services.errors import ServiceError

settings = get_settings()
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    if settings.uses_default_jwt_secret:
        logger.warning("JWT_SECRET_KEY is a placeholder or too short; set a strong secret.")
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

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(catalog.router)
    app.include_router(catalog.admin)
    app.include_router(favorites.router)
    app.include_router(purchases.cart_router)
    app.include_router(purchases.order_router)
    app.include_router(payments.router)
    app.include_router(admin.router)
    app.include_router(admin.staff_router)

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi(user: Annotated[Any, Depends(get_docs_user)]) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    async def docs(user: Annotated[Any, Depends(get_docs_user)]) -> HTMLResponse:
        schema = json.dumps(app.openapi()).replace("</", "<\\/")
        return HTMLResponse(
            "<!doctype html><html><head><title>Online Cinema FLEX API</title>"
            '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">'
            '</head><body><div id="swagger-ui"></div>'
            '<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>'
            f'<script>SwaggerUIBundle({{spec:{schema},dom_id:"#swagger-ui",persistAuthorization:true}});</script>'
            "</body></html>"
        )

    return app


app = create_app()
