"""
nodes/synthesizer.py — THE REPORT WRITER (Stage 3)
======================================================

The synthesizer runs AFTER the join of all researchers (superstep
semantics: the edge researcher -> synthesizer fires only when ALL sends
are done) and receives the merged findings + sources in the state.

It writes a Markdown report with [n] citations matching the numbered
source list — and THIS node is exactly the one streaming the tokens that
arrive in the frontend as "the report is being written live" (the
service filters via the langgraph_node metadata).
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

# Detects an appended sources/bibliography section (## Quellen,
# ### Literaturverzeichnis, ## Sources …) through to the end of the text.
_SOURCES_SECTION = re.compile(
    r"\n+#{1,3}\s*(?:Quellen|Quellenangaben|Quellenverzeichnis|Literatur(?:verzeichnis)?|Sources|References)"
    r"\s*:?\s*\n.*",
    re.IGNORECASE | re.DOTALL,
)

# Markers as a PURE TEXT LINE (without a # heading) — models also like to
# write the list without any Markdown header at all. We additionally check
# that the marker sits in the LATTER part of the text with several URLs
# after it — only then is it really a source dump and not a normal
# sentence.
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
    Removes sources sections at the end of the report — in two variants:

      1. Markdown heading ("## Quellen" or similar)
      2. Pure text line "Quellen" (without #) — only if it sits in the
         rear third and at least 2 URLs follow after it (so that a
         legitimate sentence like "Die Quellen sind vielfältig" is NOT cut).

    Plain [n] citations in the running text remain untouched.
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
    """Normalizes sources via the SHARED function (see tools.py) —
    the UI later gets the same list, so the [n] citations line up."""
    from app.agent.tools import normalize_sources

    return normalize_sources(sources)


async def synthesizer_node(state: dict, llm: BaseChatModel | None = None) -> dict:
    """
    findings + sources in → {'report': str} out.

    IMPORTANT: sources is a REDUCER channel (operator.add). If the
    synthesizer also wrote back its (deduplicated) list, the reducer
    would append it ON TOP → every source twice. That's why the service
    does the dedupe when it persists the final state.
    """
    question = state["question"]
    findings = state.get("findings", [])
    sources = _numbered_sources(state.get("sources", []))
    emit("node", node="synthesizer", status="start", findings=len(findings))

    model = llm or get_llm()
    if model is None:
        # Echo mode: blindly concatenate the findings (testable without a key).
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
    # Fallback in case the model appends a sources list anyway (models
    # are compulsive tidiers …): cut the section — the UI renders the
    # sources itself, with clickable links.
    return {
        "report": strip_sources_section(str(response.content)),
        "usage": [extract_usage(response)],
    }
