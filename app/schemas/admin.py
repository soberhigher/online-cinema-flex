from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.accounts import UserGroupEnum


class GroupChange(BaseModel):
    group: UserGroupEnum


class AdminUserOut(BaseModel):
    id: UUID
    email: str
    group: UserGroupEnum
    is_active: bool
    created_at: datetime | None


class AdminUserPage(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    per_page: int
