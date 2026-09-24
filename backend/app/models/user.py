"""
models/user.py — user model (table "users")
===========================================

The most important model: user accounts with password, role, and active status.

IMPORTANT: In SQLModel a model is simultaneously an ORM table AND a Pydantic
schema. This means: by default ALL fields are exposed in API responses —
including `hashed_password`! Of course we do NOT want that.

That is why we define SEPARATE Pydantic models for input/output in the
schemas/ folder (UserRead, UserCreate). The User model here is ONLY for the DB.

This keeps things cleanly separated:
  - Models (here)      = how the data sits in the DB
  - Schemas (schemas/) = how the data enters/leaves the API

Why roles as an enum?
---------------------
We store the role as an enum: "user" or "admin". That is type-safe (no typos
like "adnin") and self-documenting. For more fine-grained permissions you
would additionally create a `user_permissions` table — see the README section
"Extensions".
"""

import enum
import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin

# TYPE_CHECKING: imports below are only for type checkers, not at runtime.
# Prevents circular imports (RefreshToken references User and vice versa).
if TYPE_CHECKING:
    pass


class UserRole(enum.StrEnum):
    """
    Possible roles of a user.

    StrEnum (Python 3.11+) combines str + Enum: the values are
    JSON-serializable AND type-safe (you can only assign USER/ADMIN,
    not an arbitrary string). Modern replacement for the older
    `class X(str, enum.Enum)` pattern.
    """

    USER = "user"
    ADMIN = "admin"


class User(TimestampMixin, SQLModel, table=True):
    """
    Table "users" — a user account.

    Inheritance:
      TimestampMixin -> provides created_at / updated_at
      SQLModel       -> makes it an ORM table (because of table=True)
    """

    __tablename__ = "users"

    # Primary key. We use a UUID instead of an auto-increment integer, because
    # UUIDs:
    #   * reveal nothing about the number of users (security)
    #   * can be generated without a central counter (distributed, collision-safe)
    #   * are perfectly suited for public IDs
    # default_factory=uuid.uuid4 -> Python generates a UUID when the record is created.
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )

    # Email = unique (each email may only have one account).
    # index=True -> the DB creates an index, which speeds up the login lookup.
    email: str = Field(
        unique=True,
        index=True,
        nullable=False,
        max_length=255,
        # schema_extra in Field: validation for Pydantic (e.g. email format).
        # In SQLModel you can set Pydantic validators directly in Field.
    )

    # Hashed password. NEVER store the plaintext password!
    # Hashing happens in app/core/security.py via pwdlib.
    # The field name "hashed_password" instead of "password" makes that explicit.
    hashed_password: str = Field(nullable=False, exclude=True)
    # ^ exclude=True -> never serialized by SQLModel.dict()/JSON.
    #   Double protection: even if someone accidentally returns the User model,
    #   the password does not end up in the response.

    # Full name (optional, display only).
    full_name: str | None = Field(default=None, max_length=255)

    # Role: USER (default) or ADMIN. We store it in the DB as a Postgres enum
    # type (see alembic/versions/0001_initial.py -> "userrole").
    #
    # sa_column=Column(sa.Enum(...)) instead of just `Field(...)` tells SQLModel:
    # "Use this concrete SQLAlchemy column type." Two important flags on the enum:
    #   * values_callable=lambda e: [m.value for m in e]:
    #     By default SQLAlchemy uses the enum NAMES ("ADMIN"), but we want the
    #     enum VALUES ("admin") — otherwise it does not match the DB type.
    #   * create_type=False: the type is created by Alembic migrations;
    #     SQLAlchemy should NOT create it again automatically at runtime.
    role: UserRole = Field(
        default=UserRole.USER,
        sa_column=Column(
            sa.Enum(
                UserRole,
                name="userrole",
                create_type=False,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
            server_default=sa.text("'user'"),
        ),
    )

    # Active status. False = account locked (e.g. after cancellation), cannot
    # log in. Difference to "deleted": deactivated users are retained
    # (references to them stay valid), they just cannot log in.
    is_active: bool = Field(default=True, nullable=False)

    # Relationship to refresh tokens (OneToMany).
    # `Relationship` is loaded lazily; we avoid that in the async context and
    # fetch tokens explicitly via select() instead. Hence there is no
    # Relationship field here — that is cleaner with async.
    # (If you like: from sqlmodel import Relationship and then
    #  tokens: list["RefreshToken"] = Relationship(back_populates="user"))


# Helper for type checkers (chained imports):
if TYPE_CHECKING:
    # RefreshToken references User via ForeignKey — see refresh_token.py.
    pass
