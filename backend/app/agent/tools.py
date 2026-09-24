"""
agent/tools.py — Websuche als Agent-Tool (Stufe 2, PLAN.md)
============================================================

Zwei Provider, eine Funktion — dasselbe Muster wie in llm.py:

    Tavily (wenn TAVILY_API_KEY gesetzt)
        LLM-Such-API, "Standard" in der LangGraph-Welt (langchain-tavily).

    DuckDuckGo via ddgs (DEFAULT — komplett ohne API-Key!)
        Für Lern-/Dev-Zwecke völlig ausreichend; sync-Library → wir packen
        sie via asyncio.to_thread in einen Thread (blockiert den Event-Loop
        nicht — FastAPI-Golden-Regel "async def everywhere").

WICHTIG FÜR LERNENDE — Schema vs. Ausführung:
    `web_search` (das @tool-Objekt) dient nur als SCHEMA fürs LLM
    (bind_tools). Ausführen tun wir die Suche im Graph-Loop SELBST über
    `search_web()` — weil wir die rohen Quellen (list[Source]) zusätzlich
    in den State wollen, nicht nur den Text fürs ToolMessage.
"""

import asyncio
import logging
from typing import TypedDict

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


class Source(TypedDict):
    """Eine Quelle aus der Websuche — landet im State und in der DB."""

    title: str
    url: str
    snippet: str


# ----------------------------------------------------------------------------
# Provider 1: DuckDuckGo (ddgs) — kein Key nötig, deshalb Default.
# ----------------------------------------------------------------------------
async def _ddgs_search(query: str, max_results: int) -> list[Source]:
    def _sync() -> list[Source]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        try:
            raw = list(DDGS().text(query, max_results=max_results))
        except DDGSException as e:
            # "No results found" / Rate-Limit sind hier ERWARTETE Zustände
            # (z. B. sehr spezielle deutsche Such-Phrasen) — kein Stacktrace,
            # nur eine leise Info. Der Agent läuft mit leeren Treffern weiter.
            logger.info("DuckDuckGo: keine Treffer/Rate-Limit (%s): %s", query[:60], e)
            return []
        return [
            Source(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
            for r in raw
            if r.get("href")
        ]

    # ddgs ist synchron -> Threadpool, damit der Event-Loop frei bleibt.
    return await asyncio.to_thread(_sync)


# ----------------------------------------------------------------------------
# Provider 2: Tavily — aktiv, sobald ein Key konfiguriert ist.
# ----------------------------------------------------------------------------
async def _tavily_search(query: str, max_results: int) -> list[Source]:
    from langchain_tavily import TavilySearch

    result = await TavilySearch(max_results=max_results).ainvoke({"query": query})
    raw = result.get("results", [])
    return [
        Source(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", ""))
        for r in raw
        if r.get("url")
    ]


# ----------------------------------------------------------------------------
# Anbieter-Auswahl (wie get_llm(): Key vorhanden -> Tavily, sonst ddgs)
# ----------------------------------------------------------------------------
async def search_web(query: str, max_results: int = MAX_RESULTS) -> list[Source]:
    """Sucht im Web und liefert normalisierte Quellen (title/url/snippet)."""
    if settings.tavily_api_key:
        try:
            return await _tavily_search(query, max_results)
        except Exception:  # noqa: BLE001 — Fallback statt Crash
            logger.warning("Tavily-Suche fehlgeschlagen — Fallback auf DuckDuckGo", exc_info=True)
    try:
        return await _ddgs_search(query, max_results)
    except Exception:  # noqa: BLE001 — Netzwerk weg? Leere Liste, Agent läuft weiter.
        logger.warning("DuckDuckGo-Suche fehlgeschlagen (query=%s)", query, exc_info=True)
        return []


def format_sources(sources: list[Source]) -> str:
    """Formatiert Quellen als Text — so gehen sie ins ToolMessage an das LLM."""
    if not sources:
        return "Keine Treffer."
    lines = [
        f"[{i + 1}] {s['title']}\n    URL: {s['url']}\n    {s['snippet'][:300]}"
        for i, s in enumerate(sources)
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Das SCHEMA (für bind_tools) — die Ausführung macht der Graph-Loop selbst.
# ----------------------------------------------------------------------------
@tool
async def web_search(query: str) -> str:
    """Durchsucht das Web nach aktuellen Informationen zu einer Frage.

    Liefert die Top-Treffer als nummerierte Liste mit Titel, URL und
    Kurzbeschreibung. Nutze mehrdeutige Begriffe nicht zu allgemein, sondern
    formuliere eine konkrete Suchanfrage.
    """
    # Wird nur aufgerufen, falls jemand das Tool direkt ausführt (z. B. Tests).
    return format_sources(await search_web(query))


# ----------------------------------------------------------------------------
# Normierung der Quellenliste — DIE EINE Quelle der Wahrheit für Zitate
# ----------------------------------------------------------------------------
def normalize_sources(sources: list, cap: int = 60) -> list:
    """
    Dedupliziert Quellen nach URL (letzte Sichtung gewinnt, Reihenfolge =
    Position des letzten Vorkommens) und kappt auf `cap`.

    WICHTIG: Diese Funktion nutzen SYNTHESIZER (Nummerierung im Prompt)
    UND Service (persistierte Quellenliste) — nur so zeigen [n]-Zitate im
    Report und die Quellenliste der UI auf DIESELBE Quelle. Zwei getrennte
    Dedupe-Logiken hatten zur Folge, dass [7] auf der UI auf die falsche
    URL zeigte oder gar nicht verlinkbar war.
    """
    seen: dict[str, dict] = {}
    for s in reversed(sources):
        url = s.get("url", "")
        if url and url not in seen:
            seen[url] = s
    # seen.values() ist jetzt "neueste Sichtung zuerst" -> umdrehen = älteste
    # der letzten Sichtungen zuerst; dann kappen.
    return list(seen.values())[::-1][:cap]


# ----------------------------------------------------------------------------
# DOKUMENTEN-SUCHE (Stufe 5+): User-Uploads als durchsuchbares Agent-Tool
# ----------------------------------------------------------------------------
def make_document_search_tool(documents: list[dict]):
    """
    Baut ein document_search-Tool über DIE Dokumente dieses Projekts.

    Warum als Factory/Closure? Ein Tool braucht Zugriff auf die Doc-Texte,
    aber @tool-Schemas sind statisch — also erzeugen wir das Tool PRO LAUF
    mit den aktuellen Dokumenten im Closure. Naive Stichwortsuche
    (Fenster um Treffer) statt Embeddings: gut genug, null Infrastruktur.
    """
    from langchain_core.tools import tool

    docs = [(d.get("name", "dokument"), d.get("text", "")) for d in documents]

    @tool
    async def document_search(query: str) -> str:
        """Durchsucht die vom Benutzer hochgeladenen Dokumente (PDFs, Texte)
        nach Stichworten. Nutze dieses Tool, wenn die Frage sich auf Inhalte
        der hochgeladenen Dokumente bezieht oder der Benutzer nach etwas in
        seinen Dateien fragt. Gib konkrete Suchbegriffe an."""
        terms = [t.lower() for t in query.split() if len(t) > 2]
        if not terms:
            return "Bitte konkrete Suchbegriffe angeben."
        results: list[str] = []
        for name, text in docs:
            lowered = text.lower()
            best_score, best_pos = 0, -1
            # Grobe Fenster-Suche: Position mit den meisten Term-Treffern
            for term in terms:
                start = 0
                while True:
                    pos = lowered.find(term, start)
                    if pos == -1:
                        break
                    window = lowered[max(0, pos - 100) : pos + 200]
                    score = sum(window.count(t) for t in terms)
                    if score > best_score:
                        best_score, best_pos = score, pos
                    start = pos + 1
                    if best_score > len(terms) * 3:  # gut genug
                        break
            if best_pos >= 0:
                snippet = text[max(0, best_pos - 150) : best_pos + 350].replace("\n", " ")
                results.append(f"[{name}] …{snippet}…")
        if not results:
            return "Keine Treffer in den Dokumenten."
        return "\n\n".join(results[:5])

    return document_search
