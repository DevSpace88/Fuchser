"""research trace

Revision ID: b2g3h4i5j6k7
Revises: a1f2c3d4e5f6
Create Date: 2026-08-29

Stage 5+: execution history. The service records EVERY agent event
(node start/end, sub-agent finished, phases, done/error) with a timestamp
and persists it — the basis for the trace timeline in the side panel.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2g3h4i5j6k7"
down_revision: Union[str, None] = "a1f2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_projects", sa.Column("trace", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("research_projects", "trace")
