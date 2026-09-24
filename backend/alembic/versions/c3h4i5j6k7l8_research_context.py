"""research context summary

Revision ID: c3h4i5j6k7l8
Revises: b2g3h4i5j6k7
Create Date: 2026-08-29

Chat context: on creation the client sends a compact summary of the
conversation so far (questions + report excerpts). It goes into the
graph state and is presented to the supervisor + synthesizer as context —
regardless of whether earlier runs in the thread succeeded (failure
cases such as provider rate limits no longer lose their context).
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
