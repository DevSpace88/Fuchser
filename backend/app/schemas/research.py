"""
schemas/research.py — Pydantic-Ein-/Ausgabe für Recherche-Projekte
==================================================================

Wie im User-Modell (siehe models/user.py-Kommentar) trennen wir:
    Model (DB-Form)  vs.  Schema (API-Form).

ResearchCreate: nur die Frage — alles andere (id, thread_id, status, ...)
erzeugt der Server. Niemals client-seitig vertrauenswürdige Felder entgegen-
nehmen (sonst könnte ein Client sich status="done" wünschen).
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceOut(BaseModel):
    """Eine zitierte Quelle aus der Websuche."""

    title: str
    url: str
    snippet: str = ""


class ResearchCreate(BaseModel):
    """POST /research — Body zum Anlegen einer neuen Recherche."""

    question: str = Field(min_length=3, max_length=50000, description="Forschungsfrage")
    # Stufe 5: Follow-up? Dann läuft die neue Frage im SELBEN LangGraph-
    # Thread wie die Ursprungs-Recherche (der Agent "erinnert" sich).
    followup_of: UUID | None = None
    # Kompakter Gesprächsverlauf (Q/A-Paare) als Kontext — der Client baut
    # ihn aus dem Chat. Wird Supervisor + Synthesizer im Prompt präsentiert.
    context_summary: str | None = Field(default=None, max_length=8000)
    # Unterhaltung, in die diese Frage gehört (Google-AI-Studio-Modell).
    # None -> neue Unterhaltung (Titel = Frage).
    conversation_id: UUID | None = None
    # Phase 2: "quick" (Standard) oder "deep" (Langform-Bericht).
    depth: Literal["quick", "deep"] = "quick"
    # Zitierstil für deep-Reports.
    citation_style: Literal["apa", "ieee", "plain"] | None = None

    @field_validator("followup_of", "conversation_id", "context_summary", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        """Leere Strings (z. B. aus Formularfeldern) sicher in None umwandeln."""
        if v == "" or v is None:
            return None
        return v


class ResearchUpdate(BaseModel):
    """PATCH /research/{id} — Unterhaltung umbenennen."""

    title: str = Field(min_length=1, max_length=200, description="Neuer Titel (leer vermeiden)")


class UsageOut(BaseModel):
    """Token-Verbrauch eines Laufs."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0


class ResearchProjectOut(BaseModel):
    """Ausgabe eines Projekts (Liste + Detail)."""

    model_config = ConfigDict(frozen=True)  # unveränderlich nach Bau

    id: UUID
    question: str
    title: str | None = None  # Anzeigename; NULL -> Frage als Titel
    conversation_id: UUID | None = None  # zugehörige Unterhaltung
    depth: str = "quick"
    citation_style: str | None = None
    outline: dict | None = None  # Deep-Gliederung (überlebt Reloads)
    chapters: list[Any] | None = None  # fertig geschriebene Deep-Kapitel [{title, content}]
    status: str
    report: str | None = None
    error: str | None = None
    sources: list[Any] | None = None  # {title,url,snippet}-Dicts; None bis Stufe-2-Lauf
    usage: UsageOut | None = None
    parent_id: UUID | None = None  # gesetzt bei Follow-ups
    trace: list[Any] | None = None  # Ausführungs-Historie (Sidepanel)
    thread_id: UUID
    created_at: datetime
    updated_at: datetime


class ConversationOut(BaseModel):
    """Eine Unterhaltung (Chat) für die Historie."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    total_tokens: int = 0
    last_question: str | None = None


class ConversationDetail(ConversationOut):
    """Unterhaltung inkl. aller Nachrichten (chronologisch)."""

    messages: list[ResearchProjectOut] = []


class ConversationUpdate(BaseModel):
    """PATCH /conversations/{id} — Unterhaltung umbenennen."""

    title: str = Field(min_length=1, max_length=200)




class ResearchAdminOut(BaseModel):
    """Admin-Sicht: wie ResearchProjectOut, plus Besitzer-E-Mail."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    question: str
    status: str
    user_email: str
    usage: UsageOut | None = None
    parent_id: UUID | None = None
    created_at: datetime
