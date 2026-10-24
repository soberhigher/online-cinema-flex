from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class CartMovie(BaseModel):
    movie_id: UUID
    title: str
    price: Decimal
    year: int
    genres: list[str]


class CartResponse(BaseModel):
    items: list[CartMovie]
    total: Decimal


class OrderItemResponse(BaseModel):
    movie_id: UUID
    title: str
    price_at_order: Decimal


class OrderResponse(BaseModel):
    id: UUID
    status: str
    total_amount: Decimal
    items: list[OrderItemResponse]


class OrderCreationResponse(BaseModel):
    order: OrderResponse
    excluded: list[dict[str, str]]
