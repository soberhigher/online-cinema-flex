import pytest

from app.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_token,
    hash_password,
    hash_token,
    new_opaque_token,
    validate_password,
    verify_password,
)
from app.models.accounts import UserGroupEnum


def test_password_policy_accepts_valid_and_rejects_missing_classes() -> None:
    validate_password("StrongPassword9!")
    for invalid in ("short", "lowercaseonly123!", "UPPERCASEONLY123!", "NoDigitsHere!!"):
        with pytest.raises(ValueError):
            validate_password(invalid)


def test_argon2_password_hash_verifies_only_original_password() -> None:
    encoded = hash_password("StrongPassword9!")
    assert encoded != "StrongPassword9!"
    assert verify_password("StrongPassword9!", encoded)
    assert not verify_password("DifferentPassword9!", encoded)


def test_opaque_tokens_store_only_digest_and_access_jwt_has_type() -> None:
    raw, digest = new_opaque_token()
    assert raw not in digest
    assert hash_token(raw) == digest
    encoded, _ = create_access_token("user-123", UserGroupEnum.USER.value)
    assert decode_token(encoded, "access")["sub"] == "user-123"
    with pytest.raises(InvalidTokenError):
        decode_token(encoded, "refresh")


def test_role_enum_has_exact_supported_groups() -> None:
    assert {role.value for role in UserGroupEnum} == {"USER", "MODERATOR", "ADMIN"}
