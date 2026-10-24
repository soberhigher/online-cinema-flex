from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.catalog import Movie


class PaymentStatus(StrEnum):
    SUCCESSFUL = "successful"
    CANCELED = "canceled"
    REFUNDED = "refunded"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), unique=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[PaymentStatus] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stripe_checkout_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["PaymentItem"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentItem(Base):
    __tablename__ = "payment_items"
    __table_args__ = (UniqueConstraint("payment_id", "movie_id", name="uq_payment_movie"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"))
    movie_id: Mapped[UUID] = mapped_column(ForeignKey("movies.id", ondelete="RESTRICT"))
    price_at_payment: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment: Mapped[Payment] = relationship(back_populates="items")
    movie: Mapped[Movie] = relationship()
