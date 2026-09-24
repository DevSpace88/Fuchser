"""
nodes/researcher.py — DER RESEARCHER (Stufe 3: einmal pro Sub-Frage)
======================================================================

Das Herzstück: der ReAct-Loop aus Stufe 2, jetzt als Node, der PER `Send`
mehrfach INSTANZIIERT wird — jede Instanz mit ihrer eigenen Sub-Frage.

    Send("researcher", {"sub_question": "...", "question": "..."})

Damit das klappt, ohne dass parallele Instanzen sich gegenseitig
überschreiben, schreiben wir nur in REDUCER-Kanäle (findings/sources mit
operator.add): Jede Instanz liefert IHREN Beitrag, LangGraph fügt sie
im Join zusammen (Superstep-Semantik).

Der Semaphore (PLAN.md §9) drosselt parallele LLM-Calls gegen
Rate-Limits des Providers.
"""

import asyncio
import logging
from typing import TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.events import emit
from app.agent.llm import get_llm
from app.agent.tools import (
    Source,
    format_sources,
    make_document_search_tool,
    search_web,
    web_search,
)
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

# Sicherheitsnetz gegen endloses Suchen (siehe run_react_loop).
MAX_TOOL_ROUNDS = 3

# Max. gleichzeitig laufende LLM-Calls unter den parallelen Researchern.
_LLM_SEMAPHORE = asyncio.Semaphore(4)


class Finding(TypedDict):
    """Das Ergebnis EINES Researchers — Baustein für den Synthesizer."""

    sub_question: str
    answer: str


class ResearcherInput(TypedDict):
    """Eingabe-State einer Researcher-Instanz (kommt im Send-Objekt mit)."""

    question: str
    sub_question: str
    documents: list[dict]  # hochgeladene Dokumente (document_search-Tool)


async def run_react_loop(
    llm: BaseChatModel,
    query: str,
    extra_tools: list | None = None,
) -> tuple[str, list[Source], list[dict]]:
    """
    Der handgebaute ReAct-Loop (Stufe 2 — unverändert bewährt):

        LLM + Tool-Schema -> tool_calls? -> Suche ausführen ->
        Treffer als ToolMessage zurück -> wiederholen -> finale Antwort.

    Gibt (Antworttext, gesammelte Quellen, Usage-Liste) zurück.
    """
    system = SystemMessage(
        "Du bist ein Recherche-Assistent. Nutze das web_search-Tool, um "
        "aktuelle Informationen zu finden, und schreibe dann eine präzise, "
        "sachliche Antwort auf DEUTSCH (ausschließlich deutsche Schrift, "
        "keine erfundenen Schein-Anglizismen). Zitiere benutzte Quellen als "
        "[1], [2], … passend zur Trefferliste des jeweiligen Suchergebnisses. "
        "Wenn du dich auf keine Quelle stützt, sag es offen."
    )
    messages: list = [system, HumanMessage(content=query)]

    # bind_tools hängt dem Request nur die JSON-Schemas der Tools an —
    # ausgeführt werden sie im Loop unten von UNS. extra_tools: z. B.
    # document_search (nur wenn der User Dokumente hochgeladen hat).
    tools = [web_search] + (extra_tools or [])
    llm_with_tools = llm.bind_tools(tools)
    collected_sources: list[Source] = []
    usage: list[dict] = []

    for round_no in range(1, MAX_TOOL_ROUNDS + 1):
        async with _LLM_SEMAPHORE:
            response: AIMessage = await llm_with_tools.ainvoke(messages)
        messages.append(response)
        usage.append(extract_usage(response))

        if not response.tool_calls:
            # Kein Werkzeugwunsch mehr -> das ist die finale Antwort.
            return str(response.content), collected_sources, usage

        logger.info("Recherche-Runde %d: %d Tool-Call(s)", round_no, len(response.tool_calls))
        for call in response.tool_calls:
            query_arg = call.get("args", {}).get("query", "")
            emit("tool", tool=call.get("name", "?"), query=query_arg[:80], agent="researcher")
            if call.get("name") == "document_search":
                # User-Dokument durchsuchen: Ergebnis als Kontext (nicht als
                # webbasierte Quelle — es gibt keine URL zu verlinken).
                tool_obj = next(t for t in tools if getattr(t, "name", "") == "document_search")
                doc_hits = await tool_obj.ainvoke({"query": query_arg})
                messages.append(
                    ToolMessage(content=str(doc_hits), tool_call_id=call.get("id", ""))
                )
                continue
            results = await search_web(query_arg)  # echte Ausführung
            collected_sources.extend(results)
            # Dem LLM die Treffer als ToolMessage zurückgeben (Referenz über
            # tool_call_id — so weiß das Modell, zu welchem Call das gehört).
            messages.append(
                ToolMessage(
                    content=format_sources(results),
                    tool_call_id=call.get("id", ""),
                )
            )

    # Cap erreicht: ohne Tools zur finalen Antwort zwingen.
    logger.info("Max. Tool-Runden erreicht — finalisiere ohne weitere Suche.")
    async with _LLM_SEMAPHORE:
        final = await llm.ainvoke(messages)
    usage.append(extract_usage(final))
    return str(final.content), collected_sources, usage


async def researcher_node(state: ResearcherInput, llm: BaseChatModel | None = None) -> dict:
    """
    Eine Researcher-Instanz für EINE Sub-Frage.

    Rückgabe NUR in Reducer-Kanälen: {'findings': [...], 'sources': [...]}
    — genau deshalb können beliebig viele Instanzen parallel laufen.
    """
    sub_question = state["sub_question"]
    emit("node", node="researcher", status="start", sub_question=sub_question)

    model = llm or get_llm()
    if model is None:
        return {
            "findings": [Finding(sub_question=sub_question, answer="[Echo-Modus]")],
            "sources": [],
        }

    extra_tools = (
        [make_document_search_tool(state["documents"])] if state.get("documents") else None
    )
    answer, sources, usage = await run_react_loop(model, sub_question, extra_tools)
    emit(
        "node",
        node="researcher",
        status="end",
        sub_question=sub_question,
        sources=len(sources),
    )
    return {
        "findings": [Finding(sub_question=sub_question, answer=answer)],
        "sources": sources,
        "usage": usage,
    }
