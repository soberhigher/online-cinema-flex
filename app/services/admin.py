from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounts import ActivationToken, User, UserGroup, UserGroupEnum
from app.models.payments import Payment, PaymentItem
from app.models.purchases import Order, OrderItem
from app.services.auth import AuthError
from app.services.catalog import like_pattern
from app.services.payments import payment_data
from app.services.purchases import PurchaseService


def user_data(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "group": user.group.name,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _user(self, user_id: UUID) -> User:
        user = await self.session.scalar(
            select(User).options(selectinload(User.group)).where(User.id == user_id)
        )
        if user is None:
            raise AuthError(404, "User not found")
        return user

    async def list_users(self, page: int, per_page: int, email: str | None) -> dict[str, Any]:
        query = select(User)
        if email:
            query = query.where(User.email.ilike(like_pattern(email.strip().lower()), escape="\\"))
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        users = await self.session.scalars(
            query.options(selectinload(User.group))
            .order_by(User.created_at, User.email)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return {
            "items": [user_data(user) for user in users],
            "total": total or 0,
            "page": page,
            "per_page": per_page,
        }

    async def change_group(self, user_id: UUID, group: UserGroupEnum) -> dict[str, Any]:
        user = await self._user(user_id)
        if user.group.name == UserGroupEnum.ADMIN and group != UserGroupEnum.ADMIN:
            admins = await self.session.scalar(
                select(func.count(User.id))
                .join(UserGroup)
                .where(UserGroup.name == UserGroupEnum.ADMIN)
            )
            if (admins or 0) <= 1:
                raise AuthError(409, "Cannot demote the last admin")
        new_group = await self.session.scalar(select(UserGroup).where(UserGroup.name == group))
        if new_group is None:
            raise RuntimeError("User groups have not been seeded by migrations")
        user.group_id = new_group.id
        user.group = new_group
        await self.session.commit()
        return user_data(user)

    async def activate(self, user_id: UUID) -> dict[str, Any]:
        user = await self._user(user_id)
        user.is_active = True
        await self.session.execute(
            delete(ActivationToken).where(ActivationToken.user_id == user_id)
        )
        await self.session.commit()
        return user_data(user)

    async def _page(self, query: Select[Any], page: int, per_page: int) -> tuple[list[Any], int]:
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self.session.scalars(query.offset((page - 1) * per_page).limit(per_page))
        return list(rows), total or 0

    async def orders(
        self,
        page: int,
        per_page: int,
        user_id: UUID | None = None,
        status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, Any]:
        query = select(Order).options(selectinload(Order.items).selectinload(OrderItem.movie))
        if user_id is not None:
            query = query.where(Order.user_id == user_id)
        if status is not None:
            query = query.where(Order.status == status)
        if date_from is not None:
            query = query.where(Order.created_at >= date_from)
        if date_to is not None:
            query = query.where(Order.created_at <= date_to)
        rows, total = await self._page(query.order_by(Order.created_at.desc()), page, per_page)
        items = [{**PurchaseService._order_data(order), "user_id": order.user_id} for order in rows]
        return {"items": items, "total": total, "page": page, "per_page": per_page}

    async def payments(
        self,
        page: int,
        per_page: int,
        user_id: UUID | None = None,
        status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, Any]:
        query = select(Payment).options(selectinload(Payment.items).selectinload(PaymentItem.movie))
        if user_id is not None:
            query = query.where(Payment.user_id == user_id)
        if status is not None:
            query = query.where(Payment.status == status)
        if date_from is not None:
            query = query.where(Payment.created_at >= date_from)
        if date_to is not None:
            query = query.where(Payment.created_at <= date_to)
        rows, total = await self._page(query.order_by(Payment.created_at.desc()), page, per_page)
        items = [payment_data(payment) for payment in rows]
        return {"items": items, "total": total, "page": page, "per_page": per_page}

    async def cart(self, user_id: UUID) -> dict[str, object]:
        if await self.session.get(User, user_id) is None:
            raise AuthError(404, "User not found")
        return await PurchaseService(self.session).cart(user_id)
