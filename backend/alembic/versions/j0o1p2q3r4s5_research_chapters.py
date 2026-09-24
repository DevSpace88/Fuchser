"""research chapters

Revision ID: j0o1p2q3r4s5
Revises: i9n0o1p2q3r4
Create Date: 2026-08-30

Persistiert die fertig geschriebenen Deep-Report-Kapitel inkrementell,
damit ein abgebrochener Lauf (Rate-Limit / leere LLM-Antwort) die bereits
geschriebenen Kapitel nicht verliert und ein Resume sie überspringen kann.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j0o1p2q3r4s5"
down_revision: Union[str, None] = "i9n0o1p2q3r4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_projects", sa.Column("chapters", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("research_projects", "chapters")
