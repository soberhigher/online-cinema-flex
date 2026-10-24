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


@router.post("/{movie_id}", status_code=status.HTTP_201_CREATED)
async def add_favorite(
    movie_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
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


@router.delete("/{movie_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    movie_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    favorite = await session.scalar(
        select(Favorite).where(Favorite.user_id == user.id, Favorite.movie_id == movie_id)
    )
    if favorite is None:
        raise HTTPException(status_code=404, detail="Favorite not found")
    await session.delete(favorite)
    await session.commit()


@router.get("", response_model=MoviePage)
async def favorites(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
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
