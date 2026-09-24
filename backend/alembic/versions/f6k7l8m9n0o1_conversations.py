"""conversations

Revision ID: f6k7l8m9n0o1
Revises: e5j6k7l8m9n0
Create Date: 2026-08-29

Conversations as a STANDALONE entity (as in any chat program):
A conversation contains multiple messages (research_projects).
The history lists CONVERSATIONS, not individual questions — follow-ups
thereby disappear from the chat list (they are messages in the chat).

Backfill: existing parent chains are grouped into conversations
(each root + its descendants = one conversation, title = root question).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
import uuid

revision: str = "f6k7l8m9n0o1"
down_revision: Union[str, None] = "e5j6k7l8m9n0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.add_column("research_projects", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_project_conversation",
        "research_projects",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_research_projects_conversation_id", "research_projects", ["conversation_id"])

    # ---- Backfill: parent chains -> conversations ----
    conn = op.get_bind()
    projects = conn.execute(
        sa.text(
            "SELECT id, user_id, question, parent_id, created_at FROM research_projects ORDER BY created_at"
        )
    ).fetchall()
    conv_of_project: dict = {}
    title_by_root: dict = {}
    for pid, user_id, question, parent_id, created in projects:
        if parent_id and parent_id in conv_of_project:
            conv_of_project[pid] = conv_of_project[parent_id]
            continue
        # Root: new conversation (title = truncated question)
        cid = uuid.uuid4()
        title = (question or "Unterhaltung")[:200]
        conn.execute(
            sa.text(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                "VALUES (:c, :u, :t, :ts, :ts)"
            ),
            {"c": cid, "u": user_id, "t": title, "ts": created},
        )
        conv_of_project[pid] = cid
        title_by_root[cid] = title
        conn.execute(
            sa.text("UPDATE research_projects SET conversation_id = :c WHERE id = :p"),
            {"c": cid, "p": pid},
        )
    # Pull the children along
    for pid, user_id, question, parent_id, created in projects:
        if parent_id and parent_id in conv_of_project:
            conn.execute(
                sa.text("UPDATE research_projects SET conversation_id = :c WHERE id = :p"),
                {"c": conv_of_project[parent_id], "p": pid},
            )


def downgrade() -> None:
    op.drop_index("ix_research_projects_conversation_id", table_name="research_projects")
    op.drop_constraint("fk_project_conversation", "research_projects", type_="foreignkey")
    op.drop_column("research_projects", "conversation_id")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
