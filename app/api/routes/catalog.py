from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_roles
from app.core.database import get_session
from app.models.accounts import User, UserGroupEnum
from app.models.catalog import Certification, Director, Genre, Movie, Star
from app.schemas.catalog import (
    GenreOut,
    MovieInput,
    MovieOut,
    MoviePage,
    ReferenceInput,
    ReferenceOut,
)
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

SORT_FIELDS = {"name", "year", "imdb", "price", "votes"}

PUBLIC_VALIDATION: dict[int | str, dict[str, Any]] = {
    422: {"description": "Request validation failed"},
}


@router.get(
    "/api/v1/movies/",
    summary="Search and browse movies",
    response_model=MoviePage,
    responses=PUBLIC_ERRORS,
)
async def list_movies(
    service: Annotated[CatalogService, Depends(service)],
    page: Annotated[int, Query(ge=1, description="Page number, starting at 1")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page (1-100)")] = 20,
    search: Annotated[
        str | None,
        Query(description="Substring of title, description, genre, star or director name"),
    ] = None,
    genre_id: Annotated[int | None, Query(description="Only movies of this genre")] = None,
    year: Annotated[int | None, Query(description="Exact release year")] = None,
    min_imdb: Annotated[float | None, Query(ge=0, le=10, description="Minimum IMDb rating")] = None,
    sort: Annotated[
        str, Query(description="Sort field: name, year, imdb, price or votes")
    ] = "name",
    descending: Annotated[bool, Query(description="Sort in descending order")] = False,
) -> dict[str, object]:
    """Public paginated movie list. `search` matches title, description, genre, star and director
    names (case-insensitive substring). Filters combine with AND; `sort` is one of name, year,
    imdb, price, votes.
    """
    if sort not in SORT_FIELDS:
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


@router.get(
    "/api/v1/movies/genres",
    summary="List genres",
    response_model=list[GenreOut],
    responses=PUBLIC_VALIDATION,
)
async def genres(service: Annotated[CatalogService, Depends(service)]) -> list[dict[str, Any]]:
    """Public list of genres with the number of movies in each."""
    return await service.genres()


@router.get(
    "/api/v1/movies/genres/{genre_id}/movies",
    summary="Movies of a genre",
    response_model=MoviePage,
    responses=PUBLIC_ERRORS,
)
async def genre_movies(
    genre_id: int,
    service: Annotated[CatalogService, Depends(service)],
    page: Annotated[int, Query(ge=1, description="Page number, starting at 1")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page (1-100)")] = 20,
    sort: Annotated[
        str, Query(description="Sort field: name, year, imdb, price or votes")
    ] = "name",
    descending: Annotated[bool, Query(description="Sort in descending order")] = False,
) -> dict[str, object]:
    """Public paginated, sortable list of the movies in one genre. Unknown genre returns 404."""
    if sort not in SORT_FIELDS:
        raise HTTPException(status_code=422, detail="Unsupported sort field")
    try:
        return await service.genre_movies(
            genre_id, page=page, per_page=per_page, sort=sort, descending=descending
        )
    except AuthError as exc:
        raise translate(exc) from exc


@router.get(
    "/api/v1/movies/{movie_id}",
    summary="Movie details",
    response_model=MovieOut,
    responses=PUBLIC_ERRORS,
)
async def movie_detail(
    movie_id: UUID, service: Annotated[CatalogService, Depends(service)]
) -> Movie:
    """Public details of one movie with its genres, stars, directors and certification id."""
    try:
        return await service.get_movie(movie_id)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.post(
    "/movies",
    response_model=MovieOut,
    status_code=status.HTTP_201_CREATED,
    responses=AUTH_ERRORS,
    summary="Create a movie",
)
async def create_movie(
    data: MovieInput,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> Movie:
    """MODERATOR+. Creates a movie; referenced genre, star, director and certification ids must
    exist. Name, year and duration must be unique together.
    """
    try:
        return await service.create_movie(data)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.put(
    "/movies/{movie_id}", summary="Update a movie", response_model=MovieOut, responses=AUTH_ERRORS
)
async def update_movie(
    movie_id: UUID,
    data: MovieInput,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> Movie:
    """MODERATOR+. Replaces all fields of a movie. Price changes do not affect existing orders:
    order items keep the price at order time.
    """
    try:
        return await service.update_movie(movie_id, data)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.delete(
    "/movies/{movie_id}",
    summary="Delete a movie",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=AUTH_ERRORS,
)
async def delete_movie(
    movie_id: UUID,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> None:
    """MODERATOR+. Deletes a movie unless it is in a cart or an order (409)."""
    try:
        await service.delete_movie(movie_id)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.get(
    "/movies", summary="List movies for moderation", response_model=MoviePage, responses=AUTH_ERRORS
)
async def admin_list_movies(
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
    page: Annotated[int, Query(ge=1, description="Page number, starting at 1")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page (1-100)")] = 20,
) -> dict[str, object]:
    """MODERATOR+. Paginated movie list for the moderation UI."""
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
    summary="Create a reference item",
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
    """MODERATOR+. Creates a genre, star, director or certification (`resource` is genres, stars,
    directors or certifications). Duplicate names return 409.
    """
    try:
        return await service.create_reference(reference_model(resource), data.name)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.put(
    "/{resource}/{object_id}",
    summary="Rename a reference item",
    response_model=ReferenceOut,
    responses=AUTH_ERRORS,
)
async def update_reference(
    resource: str,
    object_id: int,
    data: ReferenceInput,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> object:
    """MODERATOR+. Renames a genre, star, director or certification."""
    try:
        return await service.update_reference(reference_model(resource), object_id, data.name)
    except AuthError as exc:
        raise translate(exc) from exc


@admin.get(
    "/{resource}",
    summary="List reference items",
    response_model=list[ReferenceOut],
    responses=AUTH_ERRORS,
)
async def list_references(
    resource: str,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> list[object]:
    """MODERATOR+. Lists all genres, stars, directors or certifications."""
    return await service.reference_rows(reference_model(resource))


@admin.delete(
    "/{resource}/{object_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=AUTH_ERRORS,
    summary="Delete a reference item",
)
async def delete_reference(
    resource: str,
    object_id: int,
    service: Annotated[CatalogService, Depends(service)],
    _: Annotated[User, Depends(require_roles(UserGroupEnum.MODERATOR))],
) -> None:
    """MODERATOR+. Deletes a reference item unless movies use it (409)."""
    try:
        await service.delete_reference(reference_model(resource), object_id)
    except AuthError as exc:
        raise translate(exc) from exc
