"""research followup + usage

Revision ID: a1f2c3d4e5f6
Revises: 9d4c2d7ecc54
Create Date: 2026-08-29

Stufe 5 (PLAN.md):
    * parent_id: Follow-up-Fragen verweisen auf die Ursprungs-Recherche
      (und laufen im SELBEN LangGraph-Thread → der Agent "erinnert" sich).
    * usage: Token-Verbrauch pro Lauf ({input_tokens, output_tokens, ...}).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f2c3d4e5f6"
down_revision: Union[str, None] = "9d4c2d7ecc54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_projects", sa.Column("parent_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_research_parent",
        "research_projects",
        "research_projects",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("research_projects", sa.Column("usage", sa.JSON(), nullable=True))
    op.create_index("ix_research_projects_parent_id", "research_projects", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_research_projects_parent_id", table_name="research_projects")
    op.drop_column("research_projects", "usage")
    op.drop_constraint("fk_research_parent", "research_projects", type_="foreignkey")
    op.drop_column("research_projects", "parent_id")
