"""fix_role_enum: fixes the role column (was String, becomes a Postgres enum)

Revision ID: 0002_fix_role_enum
Revises: 0001_initial
Create Date: 2026-08-06

WHAT IS GOING ON HERE (learning background)?
--------------------------------------------
In the first migration (0001_initial) I created the `role` column as
sa.String(). But SQLModel/SQLAlchemy derives a Postgres enum type named
"userrole" for the enum field in the model (`role: UserRole`).

Model says:   role is of Postgres type userrole
DB says:      role is VARCHAR(20)

This discrepancy leads to the following error on insert:
   "type 'userrole' does not exist"

This migration corrects it AFTER THE FACT:
  1. Creates the Postgres enum type userrole with the values 'user','admin'.
  2. Converts the column to that type (existing values are carried over).

Lesson: hand-written migrations are error-prone — autogenerate
(`alembic revision --autogenerate`) would have detected the enum type correctly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_fix_role_enum"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Create the Postgres enum type. With `create_type=False` we could
    #    stop SQLAlchemy from creating the type again — but here we want
    #    to create it EXPLICITLY ourselves, hence the pure SQL variant.
    #    The values must match the .value entries of the Python enum
    #    (UserRole.USER = "user", UserRole.ADMIN = "admin") EXACTLY.
    user_role_enum = sa.Enum("user", "admin", name="userrole")
    user_role_enum.create(op.get_bind(), checkfirst=True)

    # 2) REMOVE the default. Otherwise the type switch fails with
    #    "default for column cannot be cast automatically to type userrole":
    #    Postgres tries to cast the string default ('user') to the enum type,
    #    which does not work automatically during the ALTER. First drop the
    #    default, change the type, then set the default again (now typed as
    #    userrole).
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=20),
        type_=user_role_enum,
        existing_nullable=False,
        server_default=None,
        postgresql_using="role::text::userrole",
    )
    op.alter_column(
        "users",
        "role",
        existing_type=user_role_enum,
        existing_nullable=False,
        server_default=sa.text("'user'"),
    )


def downgrade() -> None:
    # Reverse path: back to VARCHAR, then drop the enum type.
    op.alter_column(
        "users",
        "role",
        existing_type=sa.Enum(name="userrole"),
        type_=sa.String(length=20),
        existing_nullable=False,
        existing_server_default=sa.text("'user'"),
        postgresql_using="role::text",
    )
    sa.Enum(name="userrole").drop(op.get_bind(), checkfirst=True)
