"""
models/conversation.py — Tabelle "conversations": Unterhaltungen als
eigständige Entität (wie in jedem Chat-Programm).
================================================================================
Eine Conversation enthält beliebig viele Nachrichten (research_projects).
Die Historie listet Conversations — Nachfragen sind Nachrichten INNERHALB
eines Chats, keine eigenen Chats mehr (User-Feedback, Google-AI-Studio-
Modell).
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class Conversation(TimestampMixin, SQLModel, table=True):
    """Eine Unterhaltung: Titel + alle zugehörigen Fragen/Recherchen."""

    __tablename__ = "conversations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True, nullable=False)

    user_id: uuid.UUID = Field(
        foreign_key="users.id",
        index=True,
        nullable=False,
        ondelete="CASCADE",
    )

    # Anzeigename (anfangs = erste Frage, gekürzt; umbenennbar).
    title: str = Field(max_length=200, sa_column=Column(sa.String(200), nullable=False))
