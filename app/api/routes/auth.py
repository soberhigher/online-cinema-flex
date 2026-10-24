from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordRequestForm

from app.api.dependencies import get_auth_service, get_current_user
from app.models.accounts import User
from app.schemas.auth import (
    Credentials,
    EmailRequest,
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
    try:
        return await service.register(body.email, body.password)
    except AuthError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/activate",
    response_model=MessageResponse,
    responses={400: {"description": "Invalid, expired, or used activation token"}},
)
async def activate(
    token: Annotated[str, Query(min_length=20)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, str]:
    try:
        return await service.activate(token)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/resend-activation",
    response_model=MessageResponse,
    responses={422: {"description": "Request validation failed"}},
)
async def resend_activation(
    body: EmailRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    return await service.resend_activation(body.email)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        401: {"description": "Invalid credentials"},
        403: {"description": "Account not activated"},
    },
)
async def login(
    body: Credentials, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    try:
        return await service.login(body.email, body.password)
    except AuthError as exc:
        _raise_http(exc)


@router.post("/token", response_model=TokenResponse, include_in_schema=False)
async def oauth_login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, str]:
    try:
        return await service.login(form.username, form.password)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={401: {"description": "Invalid or revoked refresh token"}},
)
async def refresh(
    body: RefreshRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    try:
        return await service.refresh(body.refresh_token)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/logout",
    response_model=MessageResponse,
    responses={
        401: {"description": "Invalid or revoked refresh token"},
        422: {"description": "Request validation failed"},
    },
)
async def logout(
    body: RefreshRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    try:
        return await service.logout(body.refresh_token)
    except AuthError as exc:
        _raise_http(exc)


@router.post(
    "/change-password",
    response_model=MessageResponse,
    responses={
        400: {"description": "Incorrect old password or policy violation"},
        401: {"description": "Authentication required"},
        422: {"description": "Request validation failed"},
    },
)
async def change_password(
    body: PasswordChange,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> dict[str, str]:
    try:
        return await service.change_password(user, body.old_password, body.new_password)
    except AuthError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    responses={422: {"description": "Request validation failed"}},
)
async def forgot_password(
    body: EmailRequest, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    return await service.forgot_password(body.email)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    responses={400: {"description": "Invalid or expired reset token"}},
)
async def reset_password(
    body: PasswordReset, service: Annotated[AuthService, Depends(get_auth_service)]
) -> dict[str, str]:
    try:
        return await service.reset_password(body.token, body.new_password)
    except AuthError as exc:
        _raise_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
