"""research_projects

Revision ID: 9d4c2d7ecc54
Revises: 0002_fix_role_enum
Create Date: 2026-08-29 00:26:02.728597+00:00

MANUALLY CLEANED UP — learning note:
  `alembic revision --autogenerate` compares models with the DB and proposes
  ALL differences — including ones we do NOT want:
    * The LangGraph checkpoint tables (checkpoints, checkpoint_writes, ...)
      are NOT part of our model and are managed by the AsyncPostgresSaver
      itself -> NEVER touch them here (see AGENTS.md).
    * server_default diffs on users/refresh_tokens and "unique constraint
      removed" detection are comparison artifacts, not real changes.
  Autogenerate is a DRAFT — always read and clean it up before upgrading.

This migration creates ONLY the research_projects table (see PLAN.md §4).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '9d4c2d7ecc54'
down_revision: Union[str, None] = '0002_fix_role_enum'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'research_projects',
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('thread_id', sa.Uuid(), nullable=False),
        sa.Column('question', sqlmodel.sql.sqltypes.AutoString(length=2000), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('report', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('sources', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_research_projects_id'), 'research_projects', ['id'], unique=False)
    op.create_index(op.f('ix_research_projects_status'), 'research_projects', ['status'], unique=False)
    op.create_index(op.f('ix_research_projects_thread_id'), 'research_projects', ['thread_id'], unique=False)
    op.create_index(op.f('ix_research_projects_user_id'), 'research_projects', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_research_projects_user_id'), table_name='research_projects')
    op.drop_index(op.f('ix_research_projects_thread_id'), table_name='research_projects')
    op.drop_index(op.f('ix_research_projects_status'), table_name='research_projects')
    op.drop_index(op.f('ix_research_projects_id'), table_name='research_projects')
    op.drop_table('research_projects')
