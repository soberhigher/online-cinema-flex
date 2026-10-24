from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordRequestForm

from app.api.dependencies import get_auth_service, get_current_user
from app.models.accounts import User
from app.schemas.auth import (
    Credentials,
    EmailRequest,
    LoginRequest,
    MessageResponse,
    PasswordChange,
    PasswordReset,
    RefreshRequest,
    TokenResponse,
)
from app.services.auth import AuthError, AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


def _raise_http(exc: AuthError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post(
    "/register",
    summary="Register an account",
    response_model=MessageResponse,
    status_code=201,
    responses={
        409: {"description": "Email already registered"},
        422: {"description": "Password policy violation"},
    },
)
async def register(
    body: Credentials, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    """Creates an inactive USER account and emails an activation link (valid for the configured
    number of hours). Returns 409 if the email is taken and 422 if the password violates the
    policy: 12-128 characters with lower- and uppercase letters, a digit and a special
    character.
    """
    try:
        return await service.register(body.email, body.password)
    except AuthError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/activate",
    summary="Activate an account",
    response_model=MessageResponse,
    responses={400: {"description": "Invalid, expired, or used activation token"}},
)
async def activate(
    token: Annotated[str, Query(min_length=20)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, str]:
    """Activates the account that owns the one-time token from the activation email. Expired,
    unknown or already used tokens return 400.
    """
    try:
        return await service.activate(token)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/resend-activation",
    summary="Resend the activation email",
    response_model=MessageResponse,
    responses={422: {"description": "Request validation failed"}},
)
async def resend_activation(
    body: EmailRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    """Issues a fresh activation token for an inactive account. Always answers with the same
    message so that registered emails cannot be enumerated.
    """
    return await service.resend_activation(body.email)


@router.post(
    "/login",
    summary="Log in with JSON credentials",
    response_model=TokenResponse,
    responses={
        401: {"description": "Invalid credentials"},
        403: {"description": "Account not activated"},
    },
)
async def login(
    body: LoginRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    """Exchanges email and password of an active account for an access JWT and a refresh JWT. Wrong
    credentials return 401, an account that is not activated yet returns 403.
    """
    try:
        return await service.login(body.email, body.password)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/token",
    summary="Log in with an OAuth2 password form",
    response_model=TokenResponse,
    include_in_schema=False,
)
async def oauth_login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, str]:
    """Same as login, but accepts the `application/x-www-form-urlencoded` form used by Swagger's
    Authorize dialog (`username` is the email).
    """
    try:
        return await service.login(form.username, form.password)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/refresh",
    summary="Refresh the access token",
    response_model=TokenResponse,
    responses={401: {"description": "Invalid or revoked refresh token"}},
)
async def refresh(
    body: RefreshRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    """Returns a new access token for a valid, non-revoked refresh token."""
    try:
        return await service.refresh(body.refresh_token)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/logout",
    summary="Log out",
    response_model=MessageResponse,
    responses={
        401: {"description": "Invalid or revoked refresh token"},
        422: {"description": "Request validation failed"},
    },
)
async def logout(
    body: RefreshRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    """Revokes the given refresh token so it can no longer be used."""
    try:
        return await service.logout(body.refresh_token)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/change-password",
    summary="Change the password",
    response_model=MessageResponse,
    responses={
        400: {"description": "Incorrect old password"},
        401: {"description": "Authentication required"},
        422: {"description": "Password policy violation or invalid request"},
    },
)
async def change_password(
    body: PasswordChange,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, str]:
    """Changes the caller's password after checking the old one and revokes all of the caller's
    refresh tokens.
    """
    try:
        return await service.change_password(user, body.old_password, body.new_password)
    except AuthError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/forgot-password",
    summary="Request a password reset",
    response_model=MessageResponse,
    responses={422: {"description": "Request validation failed"}},
)
async def forgot_password(
    body: EmailRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    """Emails a one-time reset token if an active account exists. The response does not reveal
    whether it does.
    """
    return await service.forgot_password(body.email)


@router.post(
    "/reset-password",
    summary="Reset the password",
    response_model=MessageResponse,
    responses={400: {"description": "Invalid or expired reset token"}},
)
async def reset_password(
    body: PasswordReset, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    """Sets a new password using a one-time reset token and revokes all refresh tokens of the
    account.
    """
    try:
        return await service.reset_password(body.token, body.new_password)
    except AuthError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
