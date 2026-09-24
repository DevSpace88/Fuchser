"""initial: users + refresh_tokens

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00

ERSTE Migration — legt die beiden Basistabellen an.

Wir schreiben diese Migration HIER VON HAND, damit du genau siehst, was passiert.
Später erzeugst du Migrationen mit `alembic revision --autogenerate -m "..."`.
Alembic generiert dann automatisch genau dieses op.create_table(...)-Zeug.

Aufbau jeder Tabelle:
  - Primärschlüssel (PK)
  - Spalten mit Typ + Constraints (NULL/NOT NULL, UNIQUE, DEFAULT, ...)
  - Fremdschlüssel (FK) verweisen auf PK einer anderen Tabelle
  - Indizes für Spalten, nach denen oft gesucht wird (Email, user_id, ...)
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
    # --- Tabelle: users ---------------------------------------------------
    # `op.create_table(...)` ist Alembics Weg, CREATE TABLE anzulegen — aber
    # datenbankübergreifend (funktioniert auf Postgres, MySQL, SQLite gleich).
    op.create_table(
        "users",
        # UUID als Primärschlüssel. server_default erzeugt einen neuen UUID
        # direkt in der DB beim Insert (postgresql-Funktion gen_random_uuid()).
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        # Rolle als Postgres-Enum-Typ "userrole" (Werte: 'user','admin').
        # SQLModel/SQLAlchemy leitet diesen Typnamen aus dem Klassen-Namen des
        # Python-Enums (UserRole -> kleingeschrieben: userrole) ab. Die .value-
        # Werte des Enums ('user','admin') müssen exakt mit den Enum-Werten hier
        # übereinstimmen, sonst schlägt das Einfügen fehl.
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
        sa.UniqueConstraint("email"),  # jede E-Mail nur einmal
        # (Der Enum-Typ userrole schränkt die Werte schon auf 'user'/'admin'
        # ein — kein zusätzlicher CHECK-Constraint nötig.)
    )
    # Indizes beschleunigen Suchen. Auf Email suchen wir beim Login IMMER,
    # also verdient Email einen Index (UniqueConstraint oben legt schon einen).
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id", "users", ["id"], unique=False)

    # --- Tabelle: refresh_tokens -----------------------------------------
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
    """Macht upgrade() rückgängig — TABellen in umgekehrter Reihenfolge droppen."""
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_users_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
