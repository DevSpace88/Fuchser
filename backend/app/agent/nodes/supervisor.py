"""
nodes/supervisor.py — DER PLANER (Stufe 3)
===========================================

Der Supervisor zerlegt die Forschungsfrage in 2–4 eigenständig
recherchierbare Sub-Fragen.

Warum HANDGEARBEITETES JSON statt llm.with_structured_output(ResearchPlan)?
    Der elegante Weg (Schema an die API -> garantiert valides JSON) zickt
    im Stream-Modus mit dem GLM-Coding-Endpoint: langchain-openai versucht,
    die Chunks INCREMENTELL als JSON zu parsen, und crasht, bevor das Modell
    überhaupt JSON liefert (PLAN.md §9, Risikofall 6 — eingetreten!).
    Lösung: Wir fordern JSON im PROMPT an, extrahieren den ersten
    {...}-Block und validieren mit Pydantic. Funktioniert mit JEDEM
    OpenAI-kompatiblen Provider — und zeigt außerdem, was
    with_structured_output intern für dich tut.
"""

import logging
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.agent.events import emit
from app.agent.llm import get_llm
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

# Cap aus PLAN.md §9: max. 4 parallele Researcher (Rate-Limits!).
MAX_SUB_QUESTIONS = 4


class ResearchPlan(BaseModel):
    """Struktur, die das LLM für die Planung liefern MUSS."""

    sub_questions: list[str] = Field(
        description="2 bis 4 konkrete, eigenständig recherchierbare Teilfragen",
        min_length=1,
        max_length=MAX_SUB_QUESTIONS,
    )


SUPERVISOR_SYSTEM = (
    "Du bist ein Recherche-Planer. Zerlege die Forschungsfrage in 2 bis "
    f"{MAX_SUB_QUESTIONS} konkrete, eigenständig recherchierbare Teilfragen, "
    "die zusammen die Frage vollständig abdecken. Jede Teilfrage muss für "
    "eine Websuche geeignet sein (spezifisch, keine Meta-Fragen)."
    '\n\nAntworte AUSSCHLIESSLICH mit JSON im Format: {"sub_questions": ["…", "…"]}'
)

# Findet den ersten {...}-Block (auch über Zeilen hinweg).
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_plan(text: str) -> ResearchPlan | None:
    """
    Extrahiert und validiert den Plan aus der Modell-Antwort.

    Getrennt als Funktion, damit Tests sie direkt füttern können.
    """
    match = _JSON_BLOCK.search(text)
    if match is None:
        return None
    try:
        plan = ResearchPlan.model_validate_json(match.group(0))
    except ValidationError:
        return None
    return plan if plan.sub_questions else None


async def supervisor_node(state: dict, llm: BaseChatModel | None = None) -> dict:
    """
    Liefert {'sub_questions': [...]} — der Grundstein fürs Send-Fan-out.

    Der Cap (max. 4) wird ZWEIMAL durchgesetzt: im Prompt UND im
    Pydantic-Modell (max_length) — traut man der KI beim Zählen nie. ;)
    """
    question = state["question"]
    emit("node", node="supervisor", status="start")
    emit("tool", tool="plan", query=f"Plant: {question[:60]}", agent="supervisor")
    emit("tool", tool="plan", query=f"Plant Recherche zu: {question[:60]}", agent="supervisor")

    model = llm or get_llm()
    if model is None:
        # Echo-Modus: die Frage selbst als einzige "Sub-Frage".
        return {"sub_questions": [question]}

    try:
        # Follow-up-Kontext (Stufe 5): Läuft dieser Graph im selben Thread
        # wie eine frühere Recherche, liegen deren findings bereits im
        # State — der Supervisor plant dann nur die NEUEN Aspekte, statt
        # alles nochmal zu recherchieren. Das ist LangGraph-Thread-Memory.
        context_parts: list[str] = []

        # 0) Hochgeladene Dokumente (document_search-fähig)
        # 1) Expliziter Chat-Verlauf vom Client (funktioniert auch nach
        #    fehlgeschlagenen Läufen — der Ursprungs-Kontext geht nie verloren).
        chat_context = (state.get("context_summary") or "").strip()
        if chat_context:
            context_parts.append(f"Bisheriger Gesprächsverlauf mit dem User:\n{chat_context}")

        # 2) Findings aus dem Thread (falls ein früherer Lauf erfolgreich war).
        documents = state.get("documents") or []
        if documents:
            doc_names = ", ".join(d.get("name", "?") for d in documents)
            context_parts.append(
                f"Der User hat {len(documents)} Dokument(e) hochgeladen: {doc_names}. "
                "Die Researcher können sie mit dem document_search-Tool "
                "durchsuchen — plane Teilfragen, die diese Dokumente nutzen!"
            )

        prior = state.get("findings") or []
        if prior:
            summary = "\n".join(
                f"- {f.get('sub_question', '')}: {f.get('answer', '')[:200]}" for f in prior[-8:]
            )
            context_parts.append(f"Bisherige Erkenntnisse aus der vorherigen Recherche:\n{summary}")

        context = ""
        if context_parts:
            context = (
                "\n\n"
                + "\n\n".join(context_parts)
                + "\n\nDie aktuelle Frage bezieht sich darauf — plane NUR die "
                "Aspekte, die noch NICHT beantwortet wurden!"
            )

        response = await model.ainvoke(
            [
                SystemMessage(content=SUPERVISOR_SYSTEM),
                HumanMessage(content=f"Forschungsfrage: {question}{context}"),
            ]
        )
        plan = parse_plan(str(response.content))
        usage = [extract_usage(response)]
    except Exception:  # noqa: BLE001 — Netzwerk/Provider-Fehler -> Fallback
        logger.warning(
            "Supervisor-Planung fehlgeschlagen — Fallback: Original-Frage",
            exc_info=True,
        )
        return {"sub_questions": [question]}

    if plan is None:
        # JSON unlesbar/unplausibel -> Fallback statt Crash.
        logger.warning("Supervisor lieferte kein gültiges JSON — Fallback: Original-Frage")
        return {"sub_questions": [question]}

    sub_questions = [q.strip() for q in plan.sub_questions if q.strip()]
    return {
        "sub_questions": sub_questions[:MAX_SUB_QUESTIONS],
        "usage": usage,
    }
