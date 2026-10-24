from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.catalog import Certification, Director, Genre, Movie, Star
from app.models.favorites import Favorite
from app.models.purchases import CartItem, OrderItem
from app.schemas.catalog import MovieInput
from app.services.auth import AuthError

SORT_COLUMNS = {
    "name": Movie.name,
    "year": Movie.year,
    "imdb": Movie.imdb,
    "price": Movie.price,
    "votes": Movie.votes,
}


ReferenceModel = Any


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def movie_query(
        self,
        *,
        search: str | None = None,
        genre_id: int | None = None,
        year: int | None = None,
        min_imdb: float | None = None,
        favorite_user_id: UUID | None = None,
    ) -> Any:
        query = select(Movie)
        if favorite_user_id is not None:
            from app.models.favorites import Favorite

            query = query.join(Favorite, Favorite.movie_id == Movie.id).where(
                Favorite.user_id == favorite_user_id
            )
        if genre_id is not None:
            query = query.join(Movie.genres).where(Genre.id == genre_id)
        if year is not None:
            query = query.where(Movie.year == year)
        if min_imdb is not None:
            query = query.where(Movie.imdb >= min_imdb)
        if search:
            term = f"%{search.strip()}%"
            if genre_id is None:
                query = query.outerjoin(Movie.genres)
            query = (
                query.outerjoin(Movie.stars)
                .outerjoin(Movie.directors)
                .outerjoin(Movie.certification)
            )
            query = query.where(
                or_(
                    Movie.name.ilike(term),
                    Movie.description.ilike(term),
                    Genre.name.ilike(term),
                    Star.name.ilike(term),
                    Director.name.ilike(term),
                    Certification.name.ilike(term),
                    cast(Movie.year, String).ilike(term),
                    cast(Movie.time, String).ilike(term),
                    cast(Movie.imdb, String).ilike(term),
                    cast(Movie.votes, String).ilike(term),
                    cast(Movie.metascore, String).ilike(term),
                    cast(Movie.gross, String).ilike(term),
                    cast(Movie.price, String).ilike(term),
                )
            )
        return query.distinct()

    async def list_movies(
        self,
        *,
        page: int = 1,
        per_page: int = 20,
        search: str | None = None,
        genre_id: int | None = None,
        year: int | None = None,
        min_imdb: float | None = None,
        sort: str = "name",
        descending: bool = False,
        favorite_user_id: UUID | None = None,
    ) -> dict[str, object]:
        query = self.movie_query(
            search=search,
            genre_id=genre_id,
            year=year,
            min_imdb=min_imdb,
            favorite_user_id=favorite_user_id,
        )
        count = await self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        column = SORT_COLUMNS[sort]
        ordering = column.desc() if descending else column.asc()
        items = (
            await self.session.scalars(
                query.options(
                    selectinload(Movie.genres),
                    selectinload(Movie.stars),
                    selectinload(Movie.directors),
                    selectinload(Movie.certification),
                )
                .order_by(ordering, Movie.id)
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).all()
        return {"items": items, "total": count, "page": page, "per_page": per_page}

    async def get_movie(self, movie_id: UUID) -> Movie:
        movie = await self.session.scalar(
            select(Movie)
            .options(
                selectinload(Movie.genres),
                selectinload(Movie.stars),
                selectinload(Movie.directors),
            )
            .where(Movie.id == movie_id)
        )
        if movie is None:
            raise AuthError(404, "Movie not found")
        return movie

    async def create_movie(self, data: MovieInput) -> Movie:
        movie = Movie(
            **data.model_dump(exclude={"genre_ids", "star_ids", "director_ids"}),
            genres=await self._references(Genre, data.genre_ids),
            stars=await self._references(Star, data.star_ids),
            directors=await self._references(Director, data.director_ids),
        )
        if (
            data.certification_id is not None
            and await self.session.get(Certification, data.certification_id) is None
        ):
            raise AuthError(404, "Certification not found")
        self.session.add(movie)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AuthError(409, "Movie conflicts with an existing catalog entry") from exc
        return await self.get_movie(movie.id)

    async def update_movie(self, movie_id: UUID, data: MovieInput) -> Movie:
        movie = await self.session.scalar(
            select(Movie)
            .options(
                selectinload(Movie.genres),
                selectinload(Movie.stars),
                selectinload(Movie.directors),
            )
            .where(Movie.id == movie_id)
        )
        if movie is None:
            raise AuthError(404, "Movie not found")
        if (
            data.certification_id is not None
            and await self.session.get(Certification, data.certification_id) is None
        ):
            raise AuthError(404, "Certification not found")
        values = data.model_dump(exclude={"genre_ids", "star_ids", "director_ids"})
        for field, value in values.items():
            setattr(movie, field, value)
        movie.genres = await self._references(Genre, data.genre_ids)
        movie.stars = await self._references(Star, data.star_ids)
        movie.directors = await self._references(Director, data.director_ids)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AuthError(409, "Movie conflicts with an existing catalog entry") from exc
        return await self.get_movie(movie.id)

    async def delete_movie(self, movie_id: UUID) -> None:
        movie = await self.session.get(Movie, movie_id)
        if movie is None:
            raise AuthError(404, "Movie not found")
        in_cart = await self.session.scalar(
            select(CartItem.id).where(CartItem.movie_id == movie_id).limit(1)
        )
        in_order = await self.session.scalar(
            select(OrderItem.id).where(OrderItem.movie_id == movie_id).limit(1)
        )
        if in_cart is not None or in_order is not None:
            raise AuthError(409, "Movie is referenced by a cart or order")
        await self.session.execute(delete(Favorite).where(Favorite.movie_id == movie_id))
        await self.session.delete(movie)
        await self.session.commit()

    async def _references(self, model: type[Any], ids: list[int]) -> list[Any]:
        if not ids:
            return []
        records: list[Any] = list(
            (await self.session.scalars(select(model).where(model.id.in_(set(ids))))).all()
        )
        if len(records) != len(set(ids)):
            raise AuthError(404, f"One or more {model.__tablename__} references do not exist")
        return records

    async def reference_rows(self, model: type[Any]) -> list[Any]:
        return list((await self.session.scalars(select(model).order_by(model.name))).all())

    async def create_reference(self, model: type[Any], name: str) -> Any:
        record = model(name=name.strip())
        self.session.add(record)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AuthError(409, "Reference name already exists") from exc
        return record

    async def update_reference(self, model: type[Any], object_id: int, name: str) -> Any:
        record = await self.session.get(model, object_id)
        if record is None:
            raise AuthError(404, "Reference not found")
        record.name = name.strip()
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AuthError(409, "Reference name already exists") from exc
        return record

    async def delete_reference(self, model: type[Any], object_id: int) -> None:
        record = await self.session.get(model, object_id)
        if record is None:
            raise AuthError(404, "Reference not found")
        if model is Certification:
            usage = select(Movie.id).where(Movie.certification_id == object_id).limit(1)
        elif model is Genre:
            usage = select(Movie.id).join(Movie.genres).where(Genre.id == object_id).limit(1)
        elif model is Star:
            usage = select(Movie.id).join(Movie.stars).where(Star.id == object_id).limit(1)
        else:
            usage = select(Movie.id).join(Movie.directors).where(Director.id == object_id).limit(1)
        if await self.session.scalar(usage) is not None:
            raise AuthError(409, "Reference is used by movies")
        await self.session.delete(record)
        await self.session.commit()

    async def genres(self) -> list[Genre]:
        return list((await self.session.scalars(select(Genre).order_by(Genre.name))).all())

    async def genre_movies(self, genre_id: int, **kwargs: Any) -> list[Movie]:
        if await self.session.get(Genre, genre_id) is None:
            raise AuthError(404, "Genre not found")
        statement = (
            select(Movie)
            .join(Movie.genres)
            .where(Genre.id == genre_id)
            .options(
                selectinload(Movie.genres),
                selectinload(Movie.stars),
                selectinload(Movie.directors),
                selectinload(Movie.certification),
            )
            .order_by(Movie.name)
        )
        return list((await self.session.scalars(statement)).all())
