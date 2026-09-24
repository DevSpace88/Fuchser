"""
models/document.py — Tabelle "documents": hochgeladene Dateien pro Recherche
==============================================================================

Beim Upload wird der Text SOFORT extrahiert und in extracted_text gesichert
(Original-Bytes speichern wir NICHT — Speicherplatz & DSGVO-Freundlichkeit).
Der Agent durchsucht später nur noch den Text, nie die Binärdatei.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin

# Hartes Limit für extrahierten Text (Zeichen) — 1 MB Text ≈ 250k Tokens.
MAX_TEXT_CHARS = 1_000_000


class Document(TimestampMixin, SQLModel, table=True):
    """Eine hochgeladene Datei mit extrahiertem Text, gehört zu EINER Recherche."""

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

    # Wie viele Zeichen der Extraktion überlebt haben (Anzeige + Debugging).
    char_count: int = Field(default=0, nullable=False)

    # Der extrahierte Volltext (gekappt auf MAX_TEXT_CHARS). nullable gehört
    # bei sa_column-Nutzung in den Column-Aufruf, nicht ins Field (SQLModel-
    # Regel — sonst crasht jeder Insert mit einer kryptischen Meldung).
    extracted_text: str = Field(sa_column=Column(sa.Text, nullable=False))
