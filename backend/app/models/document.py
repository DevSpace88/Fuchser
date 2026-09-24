"""
models/document.py — table "documents": uploaded files per research run
=======================================================================

On upload, the text is extracted IMMEDIATELY and stored in extracted_text
(we do NOT keep the original bytes — storage space & GDPR friendliness).
The agent later searches only the text, never the binary file.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin

# Hard limit for extracted text (characters) — 1 MB of text ≈ 250k tokens.
MAX_TEXT_CHARS = 1_000_000


class Document(TimestampMixin, SQLModel, table=True):
    """An uploaded file with extracted text, belonging to ONE research run."""

    __tablename__ = "documents"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True, nullable=False)

    project_id: uuid.UUID = Field(
        foreign_key="research_projects.id",
        index=True,
        nullable=False,
        ondelete="CASCADE",
    )

    filename: str = Field(nullable=False, max_length=255)
    mime_type: str = Field(nullable=False, max_length=100)
    size_bytes: int = Field(nullable=False)

    # How many characters survived extraction (display + debugging).
    char_count: int = Field(default=0, nullable=False)

    # The extracted full text (capped at MAX_TEXT_CHARS). When using
    # sa_column, nullable belongs in the Column call, not in the Field
    # (SQLModel rule — otherwise every insert crashes with a cryptic error).
    extracted_text: str = Field(sa_column=Column(sa.Text, nullable=False))
