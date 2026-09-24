"""research title

Revision ID: d4i5j6k7l8m9
Revises: c3h4i5j6k7l8
Create Date: 2026-08-29

Umbenennbare Unterhaltungen: `title` ist der Anzeigename (Listen, PDF-
Titel). NULL -> die Originalfrage wird angezeigt. Die Frage selbst wird
NIE verändert (sie ist Kontext für Follow-ups und Trace).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4i5j6k7l8m9"
down_revision: Union[str, None] = "c3h4i5j6k7l8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_projects", sa.Column("title", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("research_projects", "title")
