"""
nodes/synthesizer.py — DER REPORT-SCHREIBER (Stufe 3)
======================================================

Der Synthesizer läuft NACH dem Join aller Researcher (Superstep-Semantik:
die Kante researcher -> synthesizer feuert erst, wenn ALLE Sends fertig
sind) und bekommt im State die gemergten findings + sources.

Er schreibt einen Markdown-Report mit [n]-Zitaten passend zur nummerierten
Quellenliste — und genau DIESER Node streamt die Token, die im Frontend
als "der Report entsteht live" ankommen (Service filtert per
langgraph_node-Metadatum).
"""

import logging
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.events import emit
from app.agent.llm import get_llm
from app.agent.tools import format_sources
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

# Erkennt einen angehängten Quellen-/Literatur-Abschnitt (## Quellen,
# ### Literaturverzeichnis, ## Sources …) bis zum Textende.
_SOURCES_SECTION = re.compile(
    r"\n+#{1,3}\s*(?:Quellen|Quellenangaben|Quellenverzeichnis|Literatur(?:verzeichnis)?|Sources|References)"
    r"\s*:?\s*\n.*",
    re.IGNORECASE | re.DOTALL,
)

# Marker als PURE TEXTZEILE (ohne #-Überschrift) — Modelle schreiben die
# Liste gern auch ganz ohne Markdown-Header. Wir prüfen zusätzlich, dass
# der Marker im HINTEREN Teil liegt und dahinter mehrere URLs stehen —
# nur dann ist es wirklich ein Quellen-Dump und kein normaler Satz.
_PLAIN_MARKERS = {
    "quellen",
    "quellenangaben",
    "quellenverzeichnis",
    "literaturverzeichnis",
    "literatur",
    "sources",
    "references",
}


def strip_sources_section(report: str) -> str:
    """
    Entfernt Quellen-Abschnitte am Report-Ende — in zwei Varianten:

      1. Markdown-Überschrift ("## Quellen" o. ä.)
      2. Pure Textzeile "Quellen" (ohne #) — nur wenn sie im hinteren
         Drittel liegt und danach mindestens 2 URLs vorkommen (damit ein
         legitimer Satz wie "Die Quellen sind vielfältig" NICHT fliegt).

    Reine [n]-Zitate im Fließtext bleiben unberührt.
    """
    text = _SOURCES_SECTION.sub("", report).rstrip()
    lines = text.splitlines()
    for i in range(len(lines) - 1, max(len(lines) // 3, len(lines) - 40) - 1, -1):
        candidate = lines[i].strip().lstrip("#").strip().rstrip(":").strip().lower()
        if candidate in _PLAIN_MARKERS:
            tail = "\n".join(lines[i:])
            if tail.count("http") >= 2:
                text = "\n".join(lines[:i]).rstrip()
            break
    return text


def _numbered_sources(sources: list) -> list:
    """Normiert Quellen über die GEMEINSAME Funktion (siehe tools.py) —
    dieselbe Liste bekommt später auch die UI, damit [n]-Zitate stimmen."""
    from app.agent.tools import normalize_sources

    return normalize_sources(sources)


async def synthesizer_node(state: dict, llm: BaseChatModel | None = None) -> dict:
    """
    findings + sources rein → {'report': str} raus.

    WICHTIG: sources sind ein REDUCER-Kanal (operator.add). Würde auch der
    Synthesizer seine (deduplizierte) Liste zurückschreiben, würde der
    Reducer sie ZUSÄTZLICH anhängen → jede Quelle doppelt. Die Dedupe
    macht deshalb der Service, wenn er den finalen Stand persistiert.
    """
    question = state["question"]
    findings = state.get("findings", [])
    sources = _numbered_sources(state.get("sources", []))
    emit("node", node="synthesizer", status="start", findings=len(findings))

    model = llm or get_llm()
    if model is None:
        # Echo-Modus: Findings stumpf aneinanderhängen (testbar ohne Key).
        parts = [f"### {f['sub_question']}\n{f['answer']}" for f in findings]
        emit("node", node="synthesizer", status="end", echo=True)
        return {"report": "## Echo-Report\n\n" + "\n\n".join(parts)}

    sections = "\n\n".join(
        f"**Teilfrage:** {f['sub_question']}\n**Ergebnis:** {f['answer']}" for f in findings
    )
    chat_context = (state.get("context_summary") or "").strip()
    context_block = (
        f"\n\nBisheriger Gesprächsverlauf (kontext für die Antwort):\n{chat_context}"
        if chat_context
        else ""
    )
    prompt = (
        f"Forschungsfrage: {question}{context_block}\n\n"
        f"Recherche-Ergebnisse der Sub-Agenten:\n\n{sections}\n\n"
        f"Nummerierte Quellen (nur für deine Zitate [1], [2], …):\n{format_sources(sources)}\n\n"
        "Schreibe daraus einen zusammenhängenden Markdown-Report: Einleitung "
        "(2–3 Sätze), dann ein Abschnitt pro Teilfrage mit den wichtigsten "
        "Fakten. Zitiere im Text als [1], [2]. Schreibe auf Deutsch.\n\n"
        "WICHTIG: Schreibe KEINE Quellenliste, KEINE Literaturverzeichnis-"
        "und KEINE Links in den Report — nur die [n]-Zitate im Fließtext. "
        "Die verlinkte Quellenliste rendert die Oberfläche automatisch "
        "unter dem Report."
    )

    response = await model.ainvoke(
        [
            SystemMessage(
                "Du bist ein Analyst, der Recherche-Ergebnisse zu einem "
                "präzisen, gut lesbaren Report mit Quellen-Zitaten verdichtet."
            ),
            HumanMessage(content=prompt),
        ]
    )
    emit("node", node="synthesizer", status="end")
    # Fallback, falls das Modell doch eine Quellenliste anhängt (Modelle
    # sind stubenhochreinigend …): Abschnitt abschneiden — die UI rendert
    # die Quellen selbst, mit klickbaren Links.
    return {
        "report": strip_sources_section(str(response.content)),
        "usage": [extract_usage(response)],
    }
