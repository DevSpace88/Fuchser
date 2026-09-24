"""fix_role_enum: korrigiert die role-Spalte (war String, wird Postgres-Enum)

Revision ID: 0002_fix_role_enum
Revises: 0001_initial
Create Date: 2026-08-06

WAS PASSIERT HIER (Lern-Hintergrund)?
-------------------------------------
In der ersten Migration (0001_initial) hatte ich die `role`-Spalte als
sa.String() angelegt. SQLModel/SQLAlchemy leitet für das Enum-Feld im Modell
(`role: UserRole`) aber einen Postgres-Enum-Typ namens "userrole" ab.

Modell sagt:  role ist vom Postgres-Typ userrole
DB sagt:       role ist VARCHAR(20)

Diese Diskrepanz führt beim Einfügen zu:
   "type 'userrole' does not exist"

Diese Migration korrigiert das NACHTRÄGLICH:
  1. Legt den Postgres-Enum-Typ userrole mit den Werten 'user','admin' an.
  2. Wandelt die Spalte auf diesen Typ um (vorhandene Werte werden übernommen).

Lektion: Handgeschriebene Migrationen sind fehleranfällig — autogenerate
(`alembic revision --autogenerate`) hätte den Enum-Typ korrekt erkannt.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_fix_role_enum"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Postgres-Enum-Typ anlegen. Mit `create_type=False` könnten wir
    #    SQLAlchemy davon abhalten, den Typ nochmal anzulegen — hier wollen
    #    wir ihn aber EXPLIZIT selbst erzeugen, deshalb die pure SQL-Variante.
    #    Die Werte müssen EXAKT mit den .value-Werten des Python-Enums
    #    (UserRole.USER = "user", UserRole.ADMIN = "admin") übereinstimmen.
    user_role_enum = sa.Enum("user", "admin", name="userrole")
    user_role_enum.create(op.get_bind(), checkfirst=True)

    # 2) Default WEGNEHMEN. Sonst schlägt der Typ-Wechsel mit
    #    "default for column cannot be cast automatically to type userrole" fehl:
    #    Postgres versucht, den String-Default ('user') auf den Enum-Typ zu
    #    casten, was beim ALTER nicht automatisch klappt. Erst Default droppen,
    #    Typ ändern, Default neu setzen (jetzt typisiert als userrole).
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
    # Rückweg: wieder VARCHAR, dann Enum-Typ droppen.
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
