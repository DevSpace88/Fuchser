"""
models/research_project.py — table "research_projects" (stage 1, PLAN.md)
=========================================================================

A research project = a user's question + the state of their LangGraph run.
The model is the bridge between two worlds:

    users (SQLModel, classic)  <->  LangGraph threads (checkpointer)

  * user_id:   WHO does the research belong to? (ownership checks in the service)
  * thread_id: Under which LangGraph thread does the graph state live?
               The checkpointer stores the state under exactly this ID —
               this is how a follow-up question later finds the context too.

status is a plain VARCHAR (not a Postgres enum): the status values change with
every stage expansion (queued → planning → researching → ...), and an enum type
in the DB would require its own migration on every extension. Learning
project = deliberately taking the simpler route.
"""

import enum
import uuid

import sqlalchemy as sa
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class ResearchStatus(enum.StrEnum):
    """Lifecycle of a research run (grows with the stages)."""

    QUEUED = "queued"  # created, the graph has never run
    RUNNING = "running"  # the graph is currently running (SSE stream open)
    DONE = "done"  # report finished
    ERROR = "error"  # run failed (the error field contains the reason)


class ResearchProject(TimestampMixin, SQLModel, table=True):
    """Table "research_projects" — one research run belonging to a user."""

    __tablename__ = "research_projects"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )

    # Ownership: FK to users. Every query filters by user_id (see the service)
    # — other people's projects are invisible from the outside (404, not 403:
    # do not disclose any information about foreign IDs).
    user_id: uuid.UUID = Field(
        foreign_key="users.id",
        index=True,
        nullable=False,
        ondelete="CASCADE",
    )

    # LangGraph thread ID: connects the project to its checkpoints.
    # A separate UUID instead of the project ID, so that later multiple runs
    # (e.g. follow-ups or re-planning) can be kept cleanly separate.
    thread_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        index=True,
        nullable=False,
    )

    # The user's research question (Text for long questions / text input).
    question: str = Field(sa_column=Column(sa.Text, nullable=False))

    # User-defined display name ("title of the conversation") — controls the
    # list display and the PDF title. NULL -> the question is shown.
    # The question itself stays untouched (context for follow-ups!).
    title: str | None = Field(default=None, max_length=200)

    # Current state (see ResearchStatus).
    status: str = Field(
        default=ResearchStatus.QUEUED.value,
        max_length=32,
        index=True,
        nullable=False,
    )

    # The finished report (Markdown). NULL as long as no run has finished.
    report: str | None = Field(default=None)

    # Deep report outline (stage 3): persisted after planning, so that it
    # survives reloads and can be released.
    outline: dict | None = Field(default=None, sa_column=Column(sa.JSON, nullable=True))

    # Fully written deep report chapters: [{title, content}]. Persisted
    # INCREMENTALLY (after each chapter), so that an abort (rate limit /
    # empty LLM response) does NOT lose the chapters already written and a
    # resume can skip them.
    chapters: list | None = Field(default=None, sa_column=Column(sa.JSON, nullable=True))

    # Phase 2 "deep reports": "quick" (default) or "deep" (outline →
    # chapter research → long chapters → assembly with bibliography).
    depth: str = Field(
        default="quick",
        max_length=16,
        sa_column=Column(
            sa.String(16), nullable=False, server_default=sa.text("'quick'")
        ),
    )
    # Citation style for deep reports: "apa" | "ieee" | "plain" (NULL = ieee).
    citation_style: str | None = Field(
        default=None,
        max_length=16,
        sa_column=Column(sa.String(16), nullable=True),
    )

    # Associated conversation (Google AI Studio model): questions within the
    # same chat share the conversation_id. The history lists conversations.
    conversation_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="conversations.id",
        index=True,
        nullable=True,
        ondelete="CASCADE",
    )

    # Follow-up chain (stage 5): reference to the original research when this
    # question was asked as a follow-up to an earlier one. NULL for
    # standalone questions. SET NULL: deleting the original simply leaves the
    # follow-up "uprooted" instead of deleting it along.
    parent_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="research_projects.id",
        ondelete="SET NULL",
        index=True,
        nullable=True,
    )

    # Token usage of the (last) run: {"input_tokens": …, "output_tokens": …,
    # "total_tokens": …, "llm_calls": …}. NULL = has not run yet.
    usage: dict | None = Field(default=None, sa_column=Column(sa.JSON))

    # Error reason, in case status == error.
    error: str | None = Field(default=None)

    # Compact conversation context (questions + report excerpts) that the
    # client sends along on creation — supervisor & synthesizer see it.
    context_summary: str | None = Field(default=None, sa_column=Column(sa.Text))

    # Execution history (sidepanel timeline): list of
    # {t: ISO timestamp, event: "node"|"status"|…, ...payload}. Recorded by
    # the service during the run and persisted at the end.
    trace: list | None = Field(default=None, sa_column=Column(sa.JSON))

    # Sources of the research (list of {title,url,...} dicts). Filled from
    # stage 2/3 onwards — the column already exists so that no second
    # migration is needed. sa.JSON (instead of JSONB) is SQLite-compatible →
    # the tests keep running.
    sources: list | None = Field(default=None, sa_column=Column(sa.JSON))
