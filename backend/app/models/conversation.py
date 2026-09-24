"""
models/conversation.py — table "conversations": conversations as their own
entity (as in any chat program).
================================================================================
A conversation contains any number of messages (research_projects).
The history lists conversations — follow-ups are messages INSIDE a chat,
no longer their own chats (user feedback, Google AI Studio model).
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class Conversation(TimestampMixin, SQLModel, table=True):
    """A conversation: title + all associated questions/research runs."""

    __tablename__ = "conversations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True, nullable=False)

    user_id: uuid.UUID = Field(
        foreign_key="users.id",
        index=True,
        nullable=False,
        ondelete="CASCADE",
    )

    # Display name (initially = first question, truncated; renameable).
    title: str = Field(max_length=200, sa_column=Column(sa.String(200), nullable=False))
