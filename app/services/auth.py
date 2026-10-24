import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    new_opaque_token,
    verify_password,
)
from app.core.security import (
    validate_password as check_password_policy,
)
from app.models.accounts import (
    ActivationToken,
    PasswordResetToken,
    RefreshToken,
    User,
    UserGroup,
    UserGroupEnum,
)
from app.services.errors import ServiceError
from app.tasks.auth_tasks import send_email

logger = logging.getLogger(__name__)


def validate_password(password: str) -> None:
    try:
        check_password_policy(password)
    except ValueError as exc:
        raise ServiceError(422, str(exc)) from exc


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    def _activation_link(self, raw: str) -> str:
        base = self.settings.app_base_url.rstrip("/")
        return f"{base}{self.settings.api_v1_prefix}/auth/activate?token={raw}"

    @staticmethod
    def _queue_email(to_address: str, subject: str, body: str) -> None:
        """Queue an email; a broker outage must not fail an already committed request."""
        try:
            send_email.delay(to_address, subject, body)
        except Exception:
            logger.exception("Failed to queue email %r to %s", subject, to_address)

    async def authenticate(self, email: str, password: str) -> User | None:
        """Return the active user for valid credentials, otherwise None."""
        user = await self.session.scalar(
            select(User)
            .options(selectinload(User.group))
            .where(User.email == email.strip().lower())
        )
        password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        if not verify_password(password, password_hash) or user is None or not user.is_active:
            return None
        return user

    async def _group_id(self, name: UserGroupEnum) -> int:
        group = await self.session.scalar(select(UserGroup).where(UserGroup.name == name))
        if group is None:
            raise RuntimeError("User groups have not been seeded by migrations")
        return group.id

    async def register(self, email: str, password: str) -> dict[str, str]:
        validate_password(password)
        normalized_email = email.strip().lower()
        if await self.session.scalar(select(User.id).where(User.email == normalized_email)):
            raise ServiceError(409, "An account with this email already exists")
        user = User(
            email=normalized_email,
            password_hash=hash_password(password),
            group_id=await self._group_id(UserGroupEnum.USER),
            is_active=False,
        )
        self.session.add(user)
        raw, digest = new_opaque_token()
        expires = datetime.now(UTC) + timedelta(hours=self.settings.activation_token_ttl_hours)
        try:
            await self.session.flush()
            self.session.add(
                ActivationToken(user_id=user.id, token_hash=digest, expires_at=expires)
            )
            await self.session.commit()
        except IntegrityError as exc:
            # Two concurrent registrations with the same email: the unique index wins.
            await self.session.rollback()
            raise ServiceError(409, "An account with this email already exists") from exc
        self._queue_email(
            normalized_email, "Activate your Online Cinema account", self._activation_link(raw)
        )
        return {"message": "Registration accepted. Check your email for an activation link."}

    async def activate(self, raw: str) -> dict[str, str]:
        token = await self.session.scalar(
            select(ActivationToken).where(ActivationToken.token_hash == hash_token(raw))
        )
        if token is None or token.expires_at <= datetime.now(UTC):
            raise ServiceError(400, "Activation token is invalid or expired")
        user = await self.session.get(User, token.user_id)
        if user is None or user.is_active:
            raise ServiceError(400, "Activation token is invalid or already used")
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
            self._queue_email(
                user.email, "Activate your Online Cinema account", self._activation_link(raw)
            )
        return {"message": "If the account can be activated, an email has been sent."}

    async def login(self, email: str, password: str) -> dict[str, str]:
        user = await self.session.scalar(
            select(User)
            .options(selectinload(User.group))
            .where(User.email == email.strip().lower())
        )
        password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        if not verify_password(password, password_hash) or user is None:
            raise ServiceError(401, "Email or password is incorrect")
        if not user.is_active:
            raise ServiceError(403, "Account is not activated")
        tokens = self._issue_tokens(user)
        await self.session.commit()
        return tokens

    def _issue_tokens(self, user: User) -> dict[str, str]:
        access, _ = create_access_token(str(user.id), user.group.name)
        refresh, expires = create_refresh_token(str(user.id))
        jti = decode_token(refresh, "refresh")["jti"]
        self.session.add(RefreshToken(user_id=user.id, jti=jti, expires_at=expires))
        return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}

    async def refresh(self, raw: str) -> dict[str, str]:
        try:
            payload = decode_token(raw, "refresh")
        except InvalidTokenError as exc:
            raise ServiceError(401, "Refresh token is invalid") from exc
        token = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.jti == payload.get("jti"))
        )
        if token is not None and token.revoked_at is not None:
            # A rotated token came back: someone else may hold a copy, end every session.
            await self.session.execute(
                delete(RefreshToken).where(RefreshToken.user_id == token.user_id)
            )
            await self.session.commit()
            raise ServiceError(401, "Refresh token is invalid or revoked")
        if token is None or token.expires_at <= datetime.now(UTC):
            raise ServiceError(401, "Refresh token is invalid or revoked")
        user = await self.session.scalar(
            select(User).options(selectinload(User.group)).where(User.id == token.user_id)
        )
        if user is None or not user.is_active:
            raise ServiceError(401, "Refresh token is invalid or revoked")
        token.revoked_at = datetime.now(UTC)
        tokens = self._issue_tokens(user)
        await self.session.commit()
        return tokens

    async def logout(self, raw: str) -> dict[str, str]:
        try:
            payload = decode_token(raw, "refresh")
        except InvalidTokenError as exc:
            raise ServiceError(401, "Refresh token is invalid") from exc
        token = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.jti == payload.get("jti"))
        )
        if token is None or token.revoked_at is not None:
            raise ServiceError(401, "Refresh token is invalid or already revoked")
        token.revoked_at = datetime.now(UTC)
        await self.session.commit()
        return {"message": "Logged out"}

    async def change_password(self, user: User, old: str, new: str) -> dict[str, str]:
        if not verify_password(old, user.password_hash):
            raise ServiceError(400, "Current password is incorrect")
        validate_password(new)
        user.password_hash = hash_password(new)
        user.password_changed_at = datetime.now(UTC)
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
            self._queue_email(
                user.email,
                "Reset your Online Cinema password",
                f"Reset token: {raw}\n\nSend it to POST {self.settings.app_base_url}"
                f"{self.settings.api_v1_prefix}/auth/reset-password together with a new password.",
            )
        return {"message": "If the account exists, a password reset email has been sent."}

    async def reset_password(self, raw: str, new: str) -> dict[str, str]:
        validate_password(new)
        token = await self.session.scalar(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw))
        )
        if token is None or token.expires_at <= datetime.now(UTC):
            raise ServiceError(400, "Password reset token is invalid or expired")
        user = await self.session.get(User, token.user_id)
        if user is None:
            raise ServiceError(400, "Password reset token is invalid or expired")
        user.password_hash = hash_password(new)
        user.password_changed_at = datetime.now(UTC)
        await self.session.delete(token)
        await self.session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        await self.session.commit()
        return {"message": "Password reset"}

    async def current_user(self, encoded: str) -> User:
        try:
            payload = decode_token(encoded, "access")
        except InvalidTokenError as exc:
            raise ServiceError(401, "Invalid access token") from exc
        try:
            user_id = UUID(payload["sub"])
        except (KeyError, ValueError) as exc:
            raise ServiceError(401, "Invalid access token") from exc
        user = await self.session.scalar(
            select(User).options(selectinload(User.group)).where(User.id == user_id)
        )
        if user is None or not user.is_active:
            raise ServiceError(401, "Invalid access token")
        changed_at = user.password_changed_at
        if changed_at is not None and payload.get("iat", 0) < changed_at.timestamp():
            raise ServiceError(401, "Invalid access token")
        return user
