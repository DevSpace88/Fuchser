"""
nodes/critic.py — DER KRITIKER (Stufe 4): Qualitäts-Gate mit Lücken-Loop
=========================================================================

Der Kritiker bewertet den Report: Beantwortet er die Forschungsfrage
vollständig? Zwei mögliche Urteile (wie beim Supervisor: prompt-basiertes
JSON + Pydantic-Validierung — GLM-sicher, siehe supervisor.py):

    {"verdict": "ok"}                              -> Graph endet.
    {"verdict": "gaps", "gaps": ["…", "…"]}        -> Lücken! Die route
       nach dem Kritiker startet Researcher NUR für die Lücken-Fragen,
       der Synthesizer schreibt eine überarbeitete Version — max.
       MAX_REVISIONS Mal, sonst wird abgebrochen (Endlosschleifen-Schutz).

Das ist DER Showcase für conditional edges mit Loop — der Grund, warum
das Ding "Graph" und nicht "Pipeline" heißt.
"""

import logging
import re
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.agent.events import emit
from app.agent.llm import get_llm
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

# Wie viele Überarbeitungsrunden darf der Kritiker maximal auslösen?
MAX_REVISIONS = 2
# Pro Runde höchstens so viele Lücken-Fragen nachlegen:
MAX_GAPS = 2

# Report-Kürzung für den Prompt (Tokens sparen — der Kritiker braucht
# kein Wort für Wort, nur Struktur + Abdeckung).
_REPORT_PREVIEW_CHARS = 6000


class Critique(BaseModel):
    """Bewertung des Reports durch den Kritiker."""

    verdict: Literal["ok", "gaps"]
    gaps: list[str] = Field(
        default_factory=list,
        description="bei verdict=gaps: 1–2 neue, konkrete Teilfragen",
    )


CRITIC_SYSTEM = (
    "Du bist ein kritischer Qualitäts-Prüfer von Recherche-Reports. "
    "Prüfe, ob der Report die Forschungsfrage vollständig und mit "
    "ausreichender Tiefe beantwortet.\n\n"
    'Antworte AUSSCHLIESSLICH mit JSON: {"verdict": "ok"} '
    'oder {"verdict": "gaps", "gaps": ["…", "…"]}. '
    f"Höchstens {MAX_GAPS} Lücken-Fragen. Sei nicht überkritisch: "
    "Wenn der Report die Frage gut abdeckt, ist ok die richtige Antwort."
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_critique(text: str) -> Critique | None:
    """Extrahiert/validiert die Bewertung (analog parse_plan im Supervisor)."""
    match = _JSON_BLOCK.search(text)
    if match is None:
        return None
    try:
        critique = Critique.model_validate_json(match.group(0))
    except ValidationError:
        return None
    return critique


async def critic_node(state: dict, llm: BaseChatModel | None = None) -> dict:
    """
    Bewertet den Report → {'critique': {...}} (+ revision_count bei Lücken).

    revision_count wird NUR bei verdict=gaps erhöht — die Route danach
    entscheidet anhand des Zählers, ob noch eine Runde läuft (<= Cap)
    oder hart beendet wird.
    """
    emit("node", node="critic", status="start")

    question = state["question"]
    report = state.get("report") or ""
    revision_count = state.get("revision_count", 0)

    model = llm or get_llm()
    critique: Critique | None = None
    usage: list[dict] = []

    if model is None:
        critique = Critique(verdict="ok")  # Echo-Modus: immer zufrieden
    else:
        try:
            response = await model.ainvoke(
                [
                    SystemMessage(content=CRITIC_SYSTEM),
                    HumanMessage(
                        content=(
                            f"Forschungsfrage: {question}\n\n"
                            f"Report:\n{report[:_REPORT_PREVIEW_CHARS]}"
                        )
                    ),
                ]
            )
            critique = parse_critique(str(response.content))
            usage.append(extract_usage(response))
        except Exception:  # noqa: BLE001 — Provider-Fehler -> ok (nicht blocken)
            logger.warning("Kritiker-Call fehlgeschlagen — bewerte als ok", exc_info=True)

    if critique is None or critique.verdict not in ("ok", "gaps"):
        critique = Critique(verdict="ok")  # unlesbar -> nicht blocken

    new_revision_count = revision_count
    if critique.verdict == "gaps":
        # Lücken plausibel machen (Cap + leere Strings raus).
        critique.gaps = [g.strip() for g in critique.gaps if g.strip()][:MAX_GAPS]
        if not critique.gaps:
            critique.verdict = "ok"  # "Lücken" ohne Lücken-Fragen zählt nicht
        else:
            new_revision_count = revision_count + 1

    emit(
        "node",
        node="critic",
        status="end",
        verdict=critique.verdict,
        gaps=critique.gaps,
        revision=new_revision_count,
    )
    return {
        "critique": critique.model_dump(),
        "revision_count": new_revision_count,
        "usage": usage,
    }
