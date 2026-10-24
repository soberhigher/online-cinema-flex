from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReferenceInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)


class ReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class MovieInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    year: int = Field(ge=1888, le=2100)
    time: int = Field(gt=0)
    imdb: Decimal = Field(ge=0, le=10, decimal_places=1)
    votes: int = Field(ge=0, default=0)
    metascore: int | None = Field(default=None, ge=0, le=100)
    gross: int | None = Field(default=None, ge=0)
    description: str | None = None
    price: Decimal = Field(ge=0, decimal_places=2)
    certification_id: int | None = None
    genre_ids: list[int] = []
    star_ids: list[int] = []
    director_ids: list[int] = []


class MovieOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    year: int
    time: int
    imdb: Decimal
    votes: int
    metascore: int | None
    gross: int | None
    description: str | None
    price: Decimal
    certification_id: int | None
    genres: list[ReferenceOut]
    stars: list[ReferenceOut]
    directors: list[ReferenceOut]


class MoviePage(BaseModel):
    items: list[MovieOut]
    total: int
    page: int
    per_page: int
