from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

movie_genres = Table(
    "movie_genres",
    Base.metadata,
    Column("movie_id", Uuid, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)
movie_stars = Table(
    "movie_stars",
    Base.metadata,
    Column("movie_id", Uuid, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column("star_id", Integer, ForeignKey("stars.id", ondelete="CASCADE"), primary_key=True),
)
movie_directors = Table(
    "movie_directors",
    Base.metadata,
    Column("movie_id", Uuid, ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "director_id", Integer, ForeignKey("directors.id", ondelete="CASCADE"), primary_key=True
    ),
)


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    movies: Mapped[list["Movie"]] = relationship(secondary=movie_genres, back_populates="genres")


class Star(Base):
    __tablename__ = "stars"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    movies: Mapped[list["Movie"]] = relationship(secondary=movie_stars, back_populates="stars")


class Director(Base):
    __tablename__ = "directors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    movies: Mapped[list["Movie"]] = relationship(
        secondary=movie_directors, back_populates="directors"
    )


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    movies: Mapped[list["Movie"]] = relationship(back_populates="certification")


class Movie(Base):
    __tablename__ = "movies"
    __table_args__ = (UniqueConstraint("name", "year", "time", name="uq_movie_name_year_time"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    time: Mapped[int] = mapped_column(Integer, nullable=False)
    imdb: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False, index=True)
    votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metascore: Mapped[int | None] = mapped_column(Integer)
    gross: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    certification_id: Mapped[int | None] = mapped_column(
        ForeignKey("certifications.id", ondelete="RESTRICT")
    )
    genres: Mapped[list[Genre]] = relationship(secondary=movie_genres, back_populates="movies")
    stars: Mapped[list[Star]] = relationship(secondary=movie_stars, back_populates="movies")
    directors: Mapped[list[Director]] = relationship(
        secondary=movie_directors, back_populates="movies"
    )
    certification: Mapped[Certification | None] = relationship(back_populates="movies")
