from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.accounts import ActivationToken, User, UserGroup, UserGroupEnum
from app.services.auth import AuthError


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
            query = query.where(User.email.ilike(f"%{email.strip().lower()}%"))
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
