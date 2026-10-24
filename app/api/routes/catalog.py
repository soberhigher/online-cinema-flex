from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_roles
from app.core.database import get_session
from app.models.accounts import User, UserGroupEnum
from app.models.catalog import Certification, Director, Genre, Movie, Star
from app.schemas.catalog import MovieInput, MovieOut, MoviePage, ReferenceInput, ReferenceOut
from app.services.auth import AuthError
from app.services.catalog import CatalogService

router = APIRouter(tags=["movie catalog"])
admin = APIRouter(prefix="/api/v1/catalog-admin", tags=["catalog administration"])


async def service(session: Annotated[AsyncSession, Depends(get_session)]) -> CatalogService:
    return CatalogService(session)


def translate(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication required or access token is invalid"},
    403: {"description": "Insufficient role permissions"},
    404: {"description": "Requested catalog item or reference was not found"},
    409: {"description": "Catalog item conflicts with existing references or purchases"},
    422: {"description": "Request validation failed"},
}
PUBLIC_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Requested catalog item was not found"},
    422: {"description": "Request validation failed"},
}

PUBLIC_VALIDATION: dict[int | str, dict[str, Any]] = {
    422: {"description": "Request validation failed"},
}


@router.get("/api/v1/movies/", response_model=MoviePage, responses=PUBLIC_ERRORS)
async def list_movies(
    service: Annotated[CatalogService, Depends(service)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    search: str | None = None,
    genre_id: int | None = None,
    year: int | None = None,
    min_imdb: Annotated[float | None, Query(ge=0, le=10)] = None,
    sort: str = "name",
    descending: bool = False,
) -> dict[str, object]:
    if sort not in {"name", "year", "imdb", "price", "votes"}:
        raise HTTPException(status_code=422, detail="Unsupported sort field")
    return await service.list_movies(
        page=page,
        per_page=per_page,
        search=search,
        genre_id=genre_id,
        year=year,
        min_imdb=min_imdb,
        sort=sort,
        descending=descending,
    )


@router.get("/api/v1/movies/genres", response_model=list[ReferenceOut], responses=PUBLIC_VALIDATION)
async def genres(service: Annotated[CatalogService, Depends(service)]) -> list[Genre]:
    return await service.genres()


@router.get(
    "/api/v1/movies/genres/{genre_id}/movies",
    response_model=list[MovieOut],
    responses=PUBLIC_ERRORS,
)
async def genre_movies(
    genre_id: int, service: Annotated[CatalogService, Depends(service)]
) -> list[Movie]:
    try:
        return await service.genre_movies(genre_id)
    except AuthError as exc:
        raise translate(exc) from exc


@router.get("/api/v1/movies/{movie_id}", response_model=MovieOut, responses=PUBLIC_ERRORS)
async def movie_detail(
    movie_id: UUID, service: Annotated[CatalogService, Depends(service)]
) -> Movie:
    try:
        return await service.get_movie(movie_id)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.post(
    "/movies", response_model=MovieOut, status_code=status.HTTP_201_CREATED, responses=AUTH_ERRORS
)
async def create_movie(
    data: MovieInput,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> Movie:
    try:
        return await service.create_movie(data)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.put("/movies/{movie_id}", response_model=MovieOut, responses=AUTH_ERRORS)
async def update_movie(
    movie_id: UUID,
    data: MovieInput,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> Movie:
    try:
        return await service.update_movie(movie_id, data)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.delete("/movies/{movie_id}", status_code=status.HTTP_204_NO_CONTENT, responses=AUTH_ERRORS)
async def delete_movie(
    movie_id: UUID,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> None:
    try:
        await service.delete_movie(movie_id)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.get("/movies", response_model=MoviePage, responses=AUTH_ERRORS)
async def admin_list_movies(
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    return await service.list_movies(page=page, per_page=per_page)


REFERENCE_MODELS = {
    "genres": Genre,
    "stars": Star,
    "directors": Director,
    "certifications": Certification,
}


def reference_model(resource: str) -> type:
    model = REFERENCE_MODELS.get(resource)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown catalog resource")
    return model


@admin.post(
    "/{resource}",
    response_model=ReferenceOut,
    status_code=status.HTTP_201_CREATED,
    responses=AUTH_ERRORS,
)
async def create_reference(
    resource: str,
    data: ReferenceInput,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> object:
    try:
        return await service.create_reference(reference_model(resource), data.name)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.put("/{resource}/{object_id}", response_model=ReferenceOut, responses=AUTH_ERRORS)
async def update_reference(
    resource: str,
    object_id: int,
    data: ReferenceInput,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> object:
    try:
        return await service.update_reference(reference_model(resource), object_id, data.name)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.get("/{resource}", response_model=list[ReferenceOut], responses=AUTH_ERRORS)
async def list_references(
    resource: str,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> list[object]:
    return await service.reference_rows(reference_model(resource))


@admin.delete(
    "/{resource}/{object_id}", status_code=status.HTTP_204_NO_CONTENT, responses=AUTH_ERRORS
)
async def delete_reference(
    resource: str,
    object_id: int,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> None:
    try:
        await service.delete_reference(reference_model(resource), object_id)
    except AuthError as exc:
        raise translate(exc) from exc
