"""research context summary

Revision ID: c3h4i5j6k7l8
Revises: b2g3h4i5j6k7
Create Date: 2026-08-29

Chat-Kontext: Der Client schickt beim Anlegen eine kompakte Zusammenfassung
des bisherigen Gesprächs (Fragen + Report-Auszüge). Sie wandert in den
Graph-State und wird Supervisor + Synthesizer als Kontext präsentiert —
unabhängig davon, ob frühere Läufe im Thread erfolgreich waren (Fehler-
fälle wie Provider-Rate-Limits verlieren so keinen Kontext mehr).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3h4i5j6k7l8"
down_revision: Union[str, None] = "b2g3h4i5j6k7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_projects",
        sa.Column("context_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_projects", "context_summary")
