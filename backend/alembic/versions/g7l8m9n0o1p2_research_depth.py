"""research depth

Revision ID: g7l8m9n0o1p2
Revises: f6k7l8m9n0o1
Create Date: 2026-08-29

Phase 2 "Deep Reports": depth per question — "quick" (the previous format)
or "deep" (outline -> chapter research -> long chapters -> assembly with
a bibliography).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g7l8m9n0o1p2"
down_revision: Union[str, None] = "f6k7l8m9n0o1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_projects",
        sa.Column(
            "depth",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'quick'"),
        ),
    )
    op.add_column(
        "research_projects",
        sa.Column("citation_style", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_projects", "citation_style")
    op.drop_column("research_projects", "depth")
