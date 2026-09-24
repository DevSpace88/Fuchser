"""initial: users + refresh_tokens

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00

FIRST migration — creates the two base tables.

We write this migration HERE BY HAND so that you see exactly what happens.
Later you generate migrations with `alembic revision --autogenerate -m "..."`.
Alembic then automatically generates exactly this op.create_table(...) stuff.

Structure of every table:
  - Primary key (PK)
  - Columns with type + constraints (NULL/NOT NULL, UNIQUE, DEFAULT, ...)
  - Foreign keys (FK) referencing the PK of another table
  - Indexes for columns that are often searched (email, user_id, ...)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Table: users -----------------------------------------------------
    # `op.create_table(...)` is Alembic's way of issuing CREATE TABLE — but
    # database-agnostic (works the same on Postgres, MySQL, SQLite).
    op.create_table(
        "users",
        # UUID as primary key. server_default generates a new UUID
        # directly in the DB on insert (postgresql function gen_random_uuid()).
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        # Role as a Postgres enum type "userrole" (values: 'user','admin').
        # SQLModel/SQLAlchemy derives this type name from the class name of
        # the Python enum (UserRole -> lowercased: userrole). The enum's
        # .value entries ('user','admin') must match the enum values here
        # exactly, otherwise inserts fail.
        sa.Column(
            "role",
            sa.Enum("user", "admin", name="userrole"),
            server_default=sa.text("'user'"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),  # each email only once
        # (The enum type userrole already restricts values to 'user'/'admin'
        # — no additional CHECK constraint needed.)
    )
    # Indexes speed up searches. We ALWAYS search by email on login,
    # so email deserves an index (the UniqueConstraint above already adds one).
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id", "users", ["id"], unique=False)

    # --- Table: refresh_tokens -------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_refresh_tokens_id", "refresh_tokens", ["id"], unique=False)
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    """Reverts upgrade() — drop the tables in reverse order."""
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_users_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
