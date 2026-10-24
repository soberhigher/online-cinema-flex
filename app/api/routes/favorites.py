from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.models.accounts import User
from app.models.catalog import Movie
from app.models.favorites import Favorite
from app.schemas.catalog import MoviePage
from app.services.catalog import CatalogService

router = APIRouter(prefix="/api/v1/favorites", tags=["favorites"])


@router.post(
    "/{movie_id}",
    summary="Add a favorite",
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Movie not found"},
        409: {"description": "Already in favorites"},
        422: {"description": "Request validation failed"},
    },
)
async def add_favorite(
    movie_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    """Adds a movie to the caller's favorites. Adding it twice returns 409."""
    movie = await session.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found")
    existing = await session.scalar(
        select(Favorite.id).where(Favorite.user_id == user.id, Favorite.movie_id == movie_id)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Movie is already in favorites")
    session.add(Favorite(user_id=user.id, movie_id=movie_id))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Movie is already in favorites") from exc
    return {"message": "Added to favorites"}


@router.delete(
    "/{movie_id}",
    summary="Remove a favorite",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Authentication required"},
        404: {"description": "Favorite not found"},
        422: {"description": "Request validation failed"},
    },
)
async def remove_favorite(
    movie_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Removes a movie from the caller's favorites."""
    favorite = await session.scalar(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.movie_id == movie_id)
    )
    if favorite is None:
        raise HTTPException(status_code=404, detail="Favorite not found")
    await session.delete(favorite)
    await session.commit()


@router.get(
    "",
    summary="List favorites",
    response_model=MoviePage,
    responses={
        401: {"description": "Authentication required"},
        422: {"description": "Request validation failed"},
    },
)
async def favorites(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
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
    """The caller's favorite movies with the same search, filters, sorting and pagination as the
    public catalog.
    """
    if sort not in {"name", "year", "imdb", "price", "votes"}:
        raise HTTPException(status_code=422, detail="Unsupported sort field")
    return await CatalogService(session).list_movies(
        page=page,
        per_page=per_page,
        search=search,
        genre_id=genre_id,
        year=year,
        min_imdb=min_imdb,
        sort=sort,
        descending=descending,
        favorite_user_id=user.id,
    )
