"""
models/refresh_token.py — refresh-token table
=============================================

Why do we store refresh tokens in the DB?
-----------------------------------------
A refresh token is valid for a long time (e.g. 7 days). If it gets stolen,
an attacker could generate new access tokens for 7 days — bad.

That is why we store refresh tokens server-side, which lets us:
  1. Revoke a specific token on logout (revoked=True).
  2. Revoke the old token on rotation (new token at /refresh).
  3. Block ALL tokens of a user at once on suspicion.

Hashed instead of plaintext:
We do not store the JWT itself, but the HASH of the token. That way a DB leak
is of no use to an attacker (the hashes cannot be used as tokens). Same
pattern as with passwords.

The token ID in the JWT (`jti` = "JWT ID") is the UUID under which we find
the token in the DB. It is contained in the JWT, but the lookup happens only
server-side via the hashed form.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey
from sqlmodel import Field, SQLModel

if TYPE_CHECKING:
    # Only for type checkers; prevents circular imports at runtime.
    pass


class RefreshToken(SQLModel, table=True):
    """
    Table "refresh_tokens" — one row per issued refresh token.

    Lifecycle:
      1. On login a row is created (revoked=False, expires_at in the future).
      2. On /refresh the old row is set revoked=True and a NEW one is created ("rotation").
      3. On logout the row is set revoked=True.
      4. Expired or revoked tokens are invalid.
    """

    __tablename__ = "refresh_tokens"

    # Primary key. At the same time this is the "jti" (JWT ID) that sits in
    # the token payload. When verifying, we look it up in the DB by this ID.
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )

    # Foreign key to users.id. `ondelete="CASCADE"` means: when the user is
    # deleted, their tokens are deleted as well (no orphans).
    user_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )

    # HASH of the token (not the token itself!). See the security note above.
    # We hash with the same pwdlib function as for passwords (Argon2/bcrypt).
    # When verifying, we hash the incoming token and compare.
    token_hash: str = Field(nullable=False, unique=True, index=True)

    # When does the token expire? After this date, refresh fails (client must log in again).
    # sa_column with DateTime(timezone=True): Postgres stores TIMESTAMPTZ,
    # and asyncpg/psycopg then expect aware datetimes — consistent across the
    # whole pipeline (see also _refresh_expiry() in auth_service, which returns aware UTC).
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    # Revoked? True = token is invalid (logout, rotation, manual blocking).
    revoked: bool = Field(default=False, nullable=False)

    # When was it issued? (For audit purposes.)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
