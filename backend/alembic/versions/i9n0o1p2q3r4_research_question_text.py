"""alter research_projects.question to text

Revision ID: i9n0o1p2q3r4
Revises: h8m9n0o1p2q3
Create Date: 2026-08-29

Erlaubt lange Forschungsfragen / Prompts (bis 50.000 Zeichen) ohne VARCHAR(2000)-Begrenzung.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i9n0o1p2q3r4"
down_revision: Union[str, None] = "h8m9n0o1p2q3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "research_projects",
        "question",
        existing_type=sa.String(length=2000),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "research_projects",
        "question",
        existing_type=sa.Text(),
        type_=sa.String(length=2000),
        existing_nullable=False,
    )
