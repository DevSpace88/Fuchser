"""
core/deps.py — Auth DEPENDENCIES (authentication + authorization)
=================================================================

This file is the linchpin of access control in FastAPI.

Concept: FastAPI "Dependencies"
-------------------------------
A dependency is a function that FastAPI executes BEFORE the endpoint.
Its result is passed to the endpoint as a parameter. Classic examples:
  - Open a DB session (see core/db.py: get_session)
  - Load the user from the JWT (see below: get_current_user)

The nice thing: dependencies can be NESTED. `current_active_user` depends
on `get_current_user`. `require_admin` in turn depends on `current_active_user`.
That way you build a pipeline in which each step checks one thing.

────────────────────────────────────────────────────────────────────────────
AUTHENTICATION vs. AUTHORIZATION (important!)
────────────────────────────────────────────────────────────────────────────
  AUTHENTICATION = "Who are you?"
      -> Check email + password, issue a JWT, verify the JWT.
      -> Result: it is a known user. (The who is settled.)

  AUTHORIZATION = "Are you allowed to do this?"
      -> Does the user have the required role / permission for THIS action?
      -> Result: 200 OK or 403 Forbidden.

Both are needed: just because you are authenticated (user known) does not
mean you may do everything. A normal user must not see the user list.

In this template:
  - get_current_user      -> authentication (who are you?)
  - current_active_user   -> same + "is your account active?"
  - require_admin         -> authorization  (may you do this? -> ADMIN only)
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_token
from app.models.user import User, UserRole

# ----------------------------------------------------------------------------
# OAuth2 schema: tells FastAPI "expect a Bearer token in the Authorization header".
# ----------------------------------------------------------------------------
# tokenUrl is the endpoint where you exchange username/password for a token.
# That is why the /docs Swagger UI shows an "Authorize" button that points
# at /api/v1/auth/login/oauth (form flow) — see api/v1/auth.py.
#
# auto_error=True (default): if the header is missing, the dependency raises 401 immediately.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login/oauth")


# ----------------------------------------------------------------------------
# 1) AUTHENTICATION: verify the token -> load the user
# ----------------------------------------------------------------------------
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """
    Returns the user that belongs to the access token.

    Flow:
      1. `token` comes automatically from oauth2_scheme (Authorization header).
         If the header is missing, FastAPI has already thrown a 401.
      2. Decode the JWT + verify the signature.
      3. Read `sub` (user ID) from the payload.
      4. Load the user from the DB.
      5. The token type must be "access" (a refresh token must not work here).

    If anything fails -> HTTP 401 with WWW-Authenticate: Bearer (the standard
    response that browsers/clients interpret as "log in again").
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token konnte nicht validiert werden",
        headers={"WWW-Authenticate": "Bearer"},  # standard for Bearer auth
    )

    # 2. Decode the JWT. Any kind of failure -> 401.
    try:
        payload = decode_token(token)
    except Exception:
        raise credentials_exception from None

    # 3. Extract the sub claim (user ID).
    user_id_str = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception

    # Extra check: only allow access tokens.
    if payload.get("type") != "access":
        raise credentials_exception

    # 4. Load the user from the DB.
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        raise credentials_exception from None

    user = await session.get(User, user_id)
    if user is None:
        raise credentials_exception

    return user


# ----------------------------------------------------------------------------
# 2) AUTHENTICATION + "is the account active?"
# ----------------------------------------------------------------------------
async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Like get_current_user, BUT additionally: is the account active?

    IMPORTANT: We raise 401 (NOT 403) so that, to the outside, a deactivated
    user is indistinguishable from a "wrong token" case. Otherwise an
    attacker would learn: "Aha, this email is locked".
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token konnte nicht validiert werden",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


# ----------------------------------------------------------------------------
# 3) AUTHORIZATION: admins only
# ----------------------------------------------------------------------------
async def get_current_admin_user(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """
    AUTHORIZATION: only ADMIN may proceed.

    Authentication (who are you?) was successful — otherwise we would not
    even be here. Now comes authorization (may you do this?):
      Is the role == ADMIN?

    Answer 403 (Forbidden), not 401:
      401 = I don't know you -> logging in again helps.
      403 = I know you, BUT you may not do this -> logging in does not help.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin-Rechte erforderlich",
        )
    return current_user


# ----------------------------------------------------------------------------
# Type aliases — this is what endpoints use
# ----------------------------------------------------------------------------
# Cleaner, more readable code in the endpoint:
#
#   @router.get("/me")
#   async def me(current_user: CurrentUserDep): ...
#
#   @router.get("/users", dependencies=[Depends(RequireAdmin)])
#   async def list_users(...): ...
#
# (FastAPI-skill recommendation: always create type aliases for dependencies.)
CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
# For `dependencies=[...]` (without parameter injection) we use the function:
RequireAdmin = Depends(get_current_admin_user)
