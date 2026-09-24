"""
schemas/auth.py — Pydantic schemas for auth inputs/outputs
===========================================================

WHY separate schemas when we already have SQLModel?
---------------------------------------------------
SQLModel unites ORM + Pydantic, but that also means: the ORM model
(`User` in models/) has fields like `hashed_password`, `is_active`, `role`
that do NOT belong on the outside. If we returned the User model directly as
a response, an internal field could easily slip through (exclude=True helps
only partially).

Clean separation:
  - models/user.py        = DB state   (what gets stored?)
  - schemas/auth.py       = API state  (what goes in/out?)

One minimal schema per use case:
  - UserCreate  -> input on registration (email + password + optional name)
  - UserLogin   -> input on login (email + password)
  - UserRead    -> output at /me, /users (without the password!)
  - Token       -> a single JWT
  - TokenPair   -> access + refresh together (what /login returns)
  - RefreshIn   -> input at /refresh (just the refresh_token string)
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


# ----------------------------------------------------------------------------
# Inputs (sent by the client)
# ----------------------------------------------------------------------------
class UserCreate(BaseModel):
    """Payload for POST /auth/register."""

    # EmailStr = Pydantic validation for real email syntax.
    # (Requires the package "email-validator" — installed via pydantic[email]
    #  or email-validator. See pyproject.toml.)
    email: EmailStr
    # Min/max length as simple validation. A stricter password policy
    # (upper/lower/digit/special character) could be added via Field(pattern=...).
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class UserLogin(BaseModel):
    """Payload for POST /auth/login (JSON variant).

    NOTE: In addition, we also support the standard OAuth2 password flow
    with form data (for the Authorize button in /docs). See api/v1/auth.py.
    """

    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    """Payload for POST /auth/refresh: the (old) refresh token."""

    refresh_token: str


# ----------------------------------------------------------------------------
# Outputs (returned by the server)
# ----------------------------------------------------------------------------
# `model_config = ConfigDict(from_attributes=True)` does the following:
# It allows Pydantic to construct the schema from an ORM object
# (e.g. UserRead.model_validate(user_orm_instance)). Pydantic reads the
# attributes via getattr — handy when you return an ORM model in the response
# and want FastAPI to convert it automatically.
class UserRead(BaseModel):
    """Public user data, as returned by /me or /users.

    Deliberately contains NO password and no internal fields.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    """A single JWT, as sent in the Authorization header."""

    access_token: str
    token_type: str = "bearer"


class TokenPair(BaseModel):
    """What /login (and /refresh) return: access + refresh together.

    The client stores the refresh token so that, when the access token
    expires, it can fetch a new one without having to log in again.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # We include the user data right away — this lets the frontend display
    # the logged-in user immediately, without an extra /me call.
    user: UserRead
