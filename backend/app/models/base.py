"""
models/base.py — shared base for all table models
=================================================

Here we define a `TimestampMixin` that automatically augments EVERY table with
two columns:
  - created_at: when was the record created?
  - updated_at: when was it last modified?

That way no model has to repeat these fields — DRY (Don't Repeat Yourself).

SQLModel concept
----------------
SQLModel (by FastAPI author Sebastián Ramirez) merges TWO worlds:
  * Pydantic  -> data validation + serialization (for the API)
  * SQLAlchemy -> ORM (for the database)

A single `class User(SQLModel, table=True)` thus replaces the otherwise
separate ORM model (SQLAlchemy) and schema (Pydantic).

`table=True` activates ORM mode. Without `table=True` it is a pure Pydantic
model (useful for response/input schemas, see schemas/).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlmodel import Field, SQLModel


class TimestampMixin(SQLModel):
    """
    Mixin that adds created_at / updated_at.

    A "mixin" is a class you hand to other classes as a base, so that shared
    fields/methods live in one place.

    IMPORTANT: This class does NOT have `table=True`. It is a "base" that real
    table models inherit from. Mixin models without table=True are NOT created
    as their own table — only the fields are passed on.
    """

    # sa_column_kwargs + sa_type instead of sa_column=Column(...):
    #
    # GOTCHA: An `sa_column=Column(...)` in the mixin creates ONE concrete
    # Column instance that is bound to ONE table. As soon as a SECOND model
    # inherits the mixin, SQLAlchemy crashes ("Column already assigned").
    # sa_column_kwargs/sa_type, by contrast, are pure CONFIGURATION —
    # SQLModel builds a fresh Column from them per model. Same DDL,
    # but mixin-safe.
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={
            "server_default": func.now(),
            "nullable": False,
        },
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={
            "server_default": func.now(),
            "onupdate": func.now(),
            "nullable": False,
        },
    )
