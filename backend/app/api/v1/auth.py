"""
api/v1/auth.py — auth endpoints (/api/v1/auth/*)
================================================

This is the "door" of our app: here you can register, log in,
renew tokens and log out.

Endpoints:
  POST /auth/register        -> create a new user (public)
  POST /auth/login           -> log in, receive a token pair (JSON input)
  POST /auth/login/oauth     -> same login, but OAuth2-compliant (FORM input)
                                -> this is the endpoint the /docs Authorize button uses
  POST /auth/refresh         -> new token pair (rotation)
  POST /auth/logout          -> revoke the refresh token
  GET  /auth/me              -> currently logged-in user (protected)

Concept note — why TWO login endpoints?
---------------------------------------
FastAPI's Swagger UI (/docs) has an "Authorize" button that sends the standard
OAuth2 form (username/password as FORM-DATA) to an endpoint.
For the button to work, we need an endpoint with
OAuth2PasswordRequestForm.

For our own frontend JSON is more convenient (EmailStr + password as a JSON body),
hence the additional /auth/login (JSON).

Both call the same auth_service.authenticate — DRY.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.db import SessionDep
from app.core.deps import CurrentUserDep
from app.schemas.auth import (
    RefreshIn,
    TokenPair,
    UserCreate,
    UserLogin,
    UserRead,
)
from app.services.auth_service import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    authenticate,
    issue_token_pair,
    refresh_token_pair,
    register_user,
)
from app.services.auth_service import (
    logout as service_logout,
)

# Router with prefix + tag. The prefix is put IN FRONT of every path declared
# below: @router.post("/login") -> /api/v1/auth/login (when the parent router
# includes /api/v1 — see api/v1/router.py).
# FastAPI skill recommendation: define metadata (prefix, tags) on the router,
# NOT on include_router.
router = APIRouter(prefix="/auth", tags=["auth"])


# ----------------------------------------------------------------------------
# /auth/register — create a new user (public)
# ----------------------------------------------------------------------------
@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, session: SessionDep):
    """
    Registers a new user.

    Input (JSON):  { email, password, full_name? }
    Response:      the new user's data (without the password).

    If the email is taken: 409 Conflict.
    """
    try:
        user = await register_user(session, data)
    except EmailAlreadyExistsError:
        # Conflict: the resource (email) already exists -> 409 (not 400).
        # `from None`: we deliberately hide the internal exception because the
        # detail message is generic (no internals leak to the outside).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Diese E-Mail-Adresse ist bereits registriert.",
        ) from None
    return user


# ----------------------------------------------------------------------------
# /auth/login — login with JSON (for our frontend)
# ----------------------------------------------------------------------------
@router.post("/login", response_model=TokenPair)
async def login(data: UserLogin, session: SessionDep):
    """
    Login with a JSON body { email, password }.

    Response on success: { access_token, refresh_token, token_type, user }.
    Response on failure: 401.
    """
    try:
        user = await authenticate(session, data.email, data.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültige Anmeldedaten.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return await issue_token_pair(session, user)


# ----------------------------------------------------------------------------
# /auth/login/oauth — login with FORM-DATA (for Swagger /docs)
# ----------------------------------------------------------------------------
@router.post("/login/oauth", response_model=TokenPair)
async def login_oauth(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
):
    """
    OAuth2-compliant login via FORM-DATA.

    Strictly speaking, OAuth2 requires the "username" field. We simply
    treat `username` as the email — for the user this means: in the /docs
    Authorize dialog, just enter the email in the "username" field.

    Why FORM-DATA instead of JSON? Because the OAuth2 specification
    requires it (RFC 6749). The Authorize button in the Swagger UI sends
    form data — and the button only works with an endpoint that accepts
    OAuth2PasswordRequestForm.
    """
    try:
        user = await authenticate(session, form_data.username, form_data.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültige Anmeldedaten.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return await issue_token_pair(session, user)


# ----------------------------------------------------------------------------
# /auth/refresh — new token pair in exchange for the refresh token
# ----------------------------------------------------------------------------
@router.post("/refresh", response_model=TokenPair)
async def refresh(data: RefreshIn, session: SessionDep):
    """
    Exchanges a refresh token for a NEW token pair (rotation).

    Input:    { refresh_token: "..." }
    Response: new { access_token, refresh_token, user }.

    Fails (invalid/expired/revoked): 401.
    """
    try:
        return await refresh_token_pair(session, data.refresh_token)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh-Token ungültig oder abgelaufen. Bitte neu einloggen.",
        ) from None


# ----------------------------------------------------------------------------
# /auth/logout — revoke the refresh token
# ----------------------------------------------------------------------------
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_endpoint(request: Request, session: SessionDep):
    """
    Invalidates the refresh token.

    We read the token from the JSON body (loosely). In the frontend we simply
    send { refresh_token: "..." } along when logging out.
    """
    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    refresh_jwt = data.get("refresh_token")
    await service_logout(session, refresh_jwt)
    # 204 = No Content: success, but no body.


# ----------------------------------------------------------------------------
# /auth/me — who am I? (protected)
# ----------------------------------------------------------------------------
@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUserDep):
    """
    Returns the data of the currently logged-in user.

    This is a PROTECTED route: `CurrentUserDep` (see core/deps.py)
    performs the authentication automatically. If it fails,
    the client never even gets here but receives a 401 directly.
    """
    return current_user
