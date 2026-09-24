"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

WICHTIG FÜR LERNENDE:
  * "Revision ID" = eindeutige ID dieser Migration.
  * "Revises"     = welche Migration davor kam (Kette!).
  * "upgrade()"   = vorwärts: diese Schema-Änderung ANWENDEN.
  * "downgrade()" = rückwärts: diese Schema-Änderung ZURÜCKNEHMEN.

  Alembic merkt sich in der Tabelle `alembic_version`, welche Revision aktuell
  ist. Mit `alembic upgrade head` läuft jede noch fehlende upgrade() durch.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
