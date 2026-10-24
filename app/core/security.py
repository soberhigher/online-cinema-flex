import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import get_settings

settings = get_settings()
_password_hasher = PasswordHasher()


class InvalidTokenError(ValueError):
    """A bearer token is invalid, expired, revoked, or malformed."""


def validate_password(password: str) -> None:
    """Enforce basic complexity and length policy for user-set passwords."""
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain between 12 and 128 characters")
    if not any(char.islower() for char in password):
        raise ValueError("Password must include a lowercase letter")
    if not any(char.isupper() for char in password):
        raise ValueError("Password must include an uppercase letter")
    if not any(char.isdigit() for char in password):
        raise ValueError("Password must include a digit")
    if not any(not char.isalnum() for char in password):
        raise ValueError("Password must include a special character")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


# Verified when the email is unknown, so that login takes the same time either way.
DUMMY_PASSWORD_HASH = _password_hasher.hash(secrets.token_urlsafe(16))


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, ValueError):
        return False


def new_opaque_token() -> tuple[str, str]:
    """Return a raw URL-safe token and its non-reversible SHA-256 digest."""
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_token(raw_token)


def hash_token(raw_token: str) -> str:
    return sha256(raw_token.encode("utf-8")).hexdigest()


def create_access_token(user_id: str, role: str) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)
    encoded = jwt.encode(
        {"sub": user_id, "role": role, "type": "access", "iat": now.timestamp(), "exp": expires_at},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded, expires_at


def create_refresh_token(user_id: str) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    encoded = jwt.encode(
        {"sub": user_id, "type": "refresh", "jti": secrets.token_urlsafe(16), "exp": expires_at},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded, expires_at


def decode_token(encoded: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(encoded, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != expected_type or not payload.get("sub"):
            raise InvalidTokenError("Invalid token type or subject")
        return payload
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Invalid or expired token") from exc
