"""documents

Revision ID: e5j6k7l8m9n0
Revises: d4i5j6k7l8m9
Create Date: 2026-08-29

Document upload (user request): uploaded files (PDF/DOCX/TXT/MD/CSV)
are text-extracted on upload and stored per research. The researcher
agent can search them via the document_search tool.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5j6k7l8m9n0"
down_revision: Union[str, None] = "d4i5j6k7l8m9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["research_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_project_id", table_name="documents")
    op.drop_table("documents")
