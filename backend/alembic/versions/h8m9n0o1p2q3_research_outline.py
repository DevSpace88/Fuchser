"""research outline

Revision ID: h8m9n0o1p2q3
Revises: g7l8m9n0o1p2
Create Date: 2026-08-29

Persists the deep-report outline so that it survives page reloads
and the approval UI (Stage 3) can be shown even after a reload.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h8m9n0o1p2q3"
down_revision: Union[str, None] = "g7l8m9n0o1p2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_projects", sa.Column("outline", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("research_projects", "outline")
