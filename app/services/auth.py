from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    new_opaque_token,
    validate_password,
    verify_password,
)
from app.models.accounts import (
    ActivationToken,
    PasswordResetToken,
    RefreshToken,
    User,
    UserGroup,
    UserGroupEnum,
)
from app.tasks.auth_tasks import send_email


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def _group_id(self, name: UserGroupEnum) -> int:
        group = await self.session.scalar(select(UserGroup).where(UserGroup.name == name))
        if group is None:
            raise RuntimeError("User groups have not been seeded by migrations")
        return group.id

    async def register(self, email: str, password: str) -> dict[str, str]:
        validate_password(password)
        normalized_email = email.strip().lower()
        if await self.session.scalar(select(User.id).where(User.email == normalized_email)):
            raise AuthError(409, "An account with this email already exists")
        user = User(
            email=normalized_email,
            password_hash=hash_password(password),
            group_id=await self._group_id(UserGroupEnum.USER),
            is_active=False,
        )
        self.session.add(user)
        await self.session.flush()
        raw, digest = new_opaque_token()
        expires = datetime.now(UTC) + timedelta(hours=self.settings.activation_token_ttl_hours)
        self.session.add(ActivationToken(user_id=user.id, token_hash=digest, expires_at=expires))
        await self.session.commit()
        send_email.delay(
            normalized_email,
            "Activate your Online Cinema account",
            f"http://localhost:8000/api/v1/auth/activate?token={raw}",
        )
        return {"message": "Registration accepted. Check your email for an activation link."}

    async def activate(self, raw: str) -> dict[str, str]:
        token = await self.session.scalar(
            select(ActivationToken).where(ActivationToken.token_hash == hash_token(raw))
        )
        if token is None or token.expires_at <= datetime.now(UTC):
            raise AuthError(400, "Activation token is invalid or expired")
        user = await self.session.get(User, token.user_id)
        if user is None or user.is_active:
            raise AuthError(400, "Activation token is invalid or already used")
        user.is_active = True
        await self.session.delete(token)
        await self.session.commit()
        return {"message": "Account activated"}

    async def resend_activation(self, email: str) -> dict[str, str]:
        user = await self.session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is not None and not user.is_active:
            previous = await self.session.get(ActivationToken, user.id)
            if previous is not None:
                await self.session.delete(previous)
            raw, digest = new_opaque_token()
            expires = datetime.now(UTC) + timedelta(hours=self.settings.activation_token_ttl_hours)
            self.session.add(
                ActivationToken(user_id=user.id, token_hash=digest, expires_at=expires)
            )
            await self.session.commit()
            send_email.delay(
                user.email,
                "Activate your Online Cinema account",
                f"http://localhost:8000/api/v1/auth/activate?token={raw}",
            )
        return {"message": "If the account can be activated, an email has been sent."}

    async def login(self, email: str, password: str) -> dict[str, str]:
        user = await self.session.scalar(
            select(User)
            .options(selectinload(User.group))
            .where(User.email == email.strip().lower())
        )
        if user is None or not verify_password(password, user.password_hash):
            raise AuthError(401, "Email or password is incorrect")
        if not user.is_active:
            raise AuthError(403, "Account is not activated")
        access, _ = create_access_token(str(user.id), user.group.name)
        refresh, expires = create_refresh_token(str(user.id))
        payload = decode_token(refresh, "refresh")
        self.session.add(RefreshToken(user_id=user.id, jti=payload["jti"], expires_at=expires))
        await self.session.commit()
        return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}

    async def refresh(self, raw: str) -> dict[str, str]:
        payload = decode_token(raw, "refresh")
        token = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.jti == payload.get("jti"))
        )
        if token is None or token.revoked_at is not None or token.expires_at <= datetime.now(UTC):
            raise AuthError(401, "Refresh token is invalid or revoked")
        user = await self.session.scalar(
            select(User).options(selectinload(User.group)).where(User.id == token.user_id)
        )
        if user is None or not user.is_active:
            raise AuthError(401, "Refresh token is invalid or revoked")
        access, _ = create_access_token(str(user.id), user.group.name)
        return {"access_token": access, "refresh_token": raw, "token_type": "bearer"}

    async def logout(self, raw: str) -> dict[str, str]:
        payload = decode_token(raw, "refresh")
        token = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.jti == payload.get("jti"))
        )
        if token is None or token.revoked_at is not None:
            raise AuthError(401, "Refresh token is invalid or already revoked")
        token.revoked_at = datetime.now(UTC)
        await self.session.commit()
        return {"message": "Logged out"}

    async def change_password(self, user: User, old: str, new: str) -> dict[str, str]:
        if not verify_password(old, user.password_hash):
            raise AuthError(400, "Current password is incorrect")
        validate_password(new)
        user.password_hash = hash_password(new)
        await self.session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        await self.session.commit()
        return {"message": "Password changed; all refresh sessions were revoked"}

    async def forgot_password(self, email: str) -> dict[str, str]:
        user = await self.session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is not None and user.is_active:
            previous = await self.session.get(PasswordResetToken, user.id)
            if previous is not None:
                await self.session.delete(previous)
            raw, digest = new_opaque_token()
            expires = datetime.now(UTC) + timedelta(
                hours=self.settings.password_reset_token_ttl_hours
            )
            self.session.add(
                PasswordResetToken(user_id=user.id, token_hash=digest, expires_at=expires)
            )
            await self.session.commit()
            send_email.delay(user.email, "Reset your Online Cinema password", f"Reset token: {raw}")
        return {"message": "If the account exists, a password reset email has been sent."}

    async def reset_password(self, raw: str, new: str) -> dict[str, str]:
        validate_password(new)
        token = await self.session.scalar(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw))
        )
        if token is None or token.expires_at <= datetime.now(UTC):
            raise AuthError(400, "Password reset token is invalid or expired")
        user = await self.session.get(User, token.user_id)
        if user is None:
            raise AuthError(400, "Password reset token is invalid or expired")
        user.password_hash = hash_password(new)
        await self.session.delete(token)
        await self.session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        await self.session.commit()
        return {"message": "Password reset"}

    async def current_user(self, encoded: str) -> User:
        payload = decode_token(encoded, "access")
        try:
            user_id = UUID(payload["sub"])
        except (KeyError, ValueError) as exc:
            raise AuthError(401, "Invalid access token") from exc
        user = await self.session.scalar(
            select(User).options(selectinload(User.group)).where(User.id == user_id)
        )
        if user is None or not user.is_active:
            raise AuthError(401, "Invalid access token")
        return user
