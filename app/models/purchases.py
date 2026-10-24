from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.catalog import Movie


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    CANCELED = "canceled"


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart", cascade="all, delete-orphan"
    )


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "movie_id", name="uq_cart_movie"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[UUID] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"))
    movie_id: Mapped[UUID] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cart: Mapped[Cart] = relationship(back_populates="items")
    movie: Mapped[Movie] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[OrderStatus] = mapped_column(String(16), default=OrderStatus.PENDING)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (UniqueConstraint("order_id", "movie_id", name="uq_order_movie"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    movie_id: Mapped[UUID] = mapped_column(ForeignKey("movies.id", ondelete="RESTRICT"))
    price_at_order: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    order: Mapped[Order] = relationship(back_populates="items")
    movie: Mapped[Movie] = relationship()
