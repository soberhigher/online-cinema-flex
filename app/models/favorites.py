from uuid import UUID

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.catalog import Movie


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "movie_id", name="uq_favorite_user_movie"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    movie_id: Mapped[UUID] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), nullable=False
    )
    movie: Mapped[Movie] = relationship()
