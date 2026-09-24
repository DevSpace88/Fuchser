"""
models/research_project.py — Tabelle "research_projects" (Stufe 1, PLAN.md)
=============================================================================

Ein Recherche-Projekt = die Frage eines Users + der Zustand seines
LangGraph-Laufs. Das Model ist die Brücke zwischen zwei Welten:

    users (SQLModel, klassisch)  <->  LangGraph-Threads (Checkpointer)

  * user_id:   WEM gehört die Recherche? (Ownership-Checks im Service)
  * thread_id: Unter welchem LangGraph-Thread liegt der Graph-State?
               Der Checkpointer speichert den State zu genau dieser ID —
               darüber findet später auch eine Follow-up-Frage den Kontext.

status ist ein simpler VARCHAR (kein Postgres-Enum): Die Status-Werte ändern sich mit
jedem Stufen-Ausbau (queued → planning → researching → ...), ein Enum-Typ in
der DB wäre bei jeder Erweiterung eine eigene Migration. Lern-Projekt =
bewusst einfacher Weg.
"""

import enum
import uuid

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class ResearchStatus(enum.StrEnum):
    """Lebenszyklus einer Recherche (wächst mit den Stufen)."""

    QUEUED = "queued"  # angelegt, Graph lief noch nie
    RUNNING = "running"  # Graph läuft gerade (SSE-Stream offen)
    DONE = "done"  # Report fertig
    ERROR = "error"  # Lauf gescheitert (error-Feld enthält Grund)


class ResearchProject(TimestampMixin, SQLModel, table=True):
    """Tabelle "research_projects" — eine Recherche eines Users."""

    __tablename__ = "research_projects"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )

    # Ownership: FK auf users. Jede Query filtert nach user_id (siehe Service)
    # — fremde Projekte sind nach außen unsichtbar (404, nicht 403: keine
    # Information über fremde IDs preisgeben).
    user_id: uuid.UUID = Field(
        foreign_key="users.id",
        index=True,
        nullable=False,
        ondelete="CASCADE",
    )

    # LangGraph-Thread-ID: verbindet das Projekt mit seinen Checkpoints.
    # Eigener UUID statt Projekt-ID, damit man später mehrere Läufe (z. B.
    # Follow-ups oder Re-Plannung) sauber trennen kann.
    thread_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        index=True,
        nullable=False,
    )

    # Die Forschungsfrage des Users (Text für lange Fragen/Texteingaben).
    question: str = Field(sa_column=Column(sa.Text, nullable=False))

    # Benutzerdefinierter Anzeigename ("Titel der Unterhaltung") — steuert
    # Listenanzeige und PDF-Titel. NULL -> die Frage wird angezeigt.
    # Die Frage selbst bleibt unangetastet (Kontext für Follow-ups!).
    title: str | None = Field(default=None, max_length=200)

    # Aktueller Zustand (siehe ResearchStatus).
    status: str = Field(
        default=ResearchStatus.QUEUED.value,
        max_length=32,
        index=True,
        nullable=False,
    )

    # Der fertige Report (Markdown). NULL solange kein Lauf fertig ist.
    report: str | None = Field(default=None)

    # Deep-Report-Gliederung (Stage 3): wird nach der Planung persistiert,
    # damit sie Reloads überlebt und freigegeben werden kann.
    outline: dict | None = Field(default=None, sa_column=Column(sa.JSON, nullable=True))

    # Fertig geschriebene Deep-Report-Kapitel: [{title, content}]. Wird
    # INKREMENTELL persistiert (nach jedem Kapitel), damit ein Abbruch
    # (Rate-Limit / leere LLM-Antwort) die bereits geschriebenen Kapitel
    # NICHT verliert und ein Resume sie überspringen kann.
    chapters: list | None = Field(default=None, sa_column=Column(sa.JSON, nullable=True))

    # Phase 2 "Deep Reports": "quick" (Standard) oder "deep" (Outline →
    # Kapitel-Recherche → lange Kapitel → Assembly mit Literaturverzeichnis).
    depth: str = Field(
        default="quick",
        max_length=16,
        sa_column=Column(
            sa.String(16), nullable=False, server_default=sa.text("'quick'")
        ),
    )
    # Zitierstil für deep-Reports: "apa" | "ieee" | "plain" (NULL = ieee).
    citation_style: str | None = Field(
        default=None,
        max_length=16,
        sa_column=Column(sa.String(16), nullable=True),
    )

    # Zugehörige Unterhaltung (Google-AI-Studio-Modell): Fragen desselben
    # Chats teilen die conversation_id. Die Historie listet Conversations.
    conversation_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="conversations.id",
        index=True,
        nullable=True,
        ondelete="CASCADE",
    )

    # Follow-up-Kette (Stufe 5): Verweis auf die Ursprungs-Recherche, wenn
    # diese Frage als Nachfrage zu einer früheren gestellt wurde. NULL bei
    # eigenständigen Fragen. SET NULL: Löscht man das Original, bleibt die
    # Nachfrage einfach "entwurzelt" stehen statt mitzulöschen.
    parent_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="research_projects.id",
        ondelete="SET NULL",
        index=True,
        nullable=True,
    )

    # Token-Verbrauch des (letzten) Laufs: {"input_tokens": …, "output_tokens": …,
    # "total_tokens": …, "llm_calls": …}. NULL = noch nicht gelaufen.
    usage: dict | None = Field(default=None, sa_column=Column(sa.JSON))

    # Fehlergrund, falls status == error.
    error: str | None = Field(default=None)

    # Kompakter Gesprächs-Kontext (Fragen + Report-Auszüge), den der
    # Client beim Anlegen mitgibt — Supervisor & Synthesizer sehen ihn.
    context_summary: str | None = Field(default=None, sa_column=Column(sa.Text))

    # Ausführungs-Historie (Sidepanel-Timeline): Liste von
    # {t: ISO-Zeitstempel, event: "node"|"status"|…, ...payload}. Wird vom
    # Service beim Lauf aufgezeichnet und am Ende persistiert.
    trace: list | None = Field(default=None, sa_column=Column(sa.JSON))

    # Quellen der Recherche (Liste von {title,url,...}-Dicts). Ab Stufe 2/3
    # gefüllt — die Spalte existiert schon, damit keine zweite Migration nötig
    # wird. sa.JSON (statt JSONB) ist SQLite-kompatibel → Tests laufen weiter.
    sources: list | None = Field(default=None, sa_column=Column(sa.JSON))
