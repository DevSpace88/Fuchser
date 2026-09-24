"""
agent/tools.py — web search as an agent tool (Stage 2, PLAN.md)
============================================================

Two providers, one function — the same pattern as in llm.py:

    Tavily (if TAVILY_API_KEY is set)
        LLM search API, the "standard" in the LangGraph world (langchain-tavily).

    DuckDuckGo via ddgs (DEFAULT — entirely without an API key!)
        Perfectly sufficient for learning/dev purposes; sync library → we
        put it into a thread via asyncio.to_thread (doesn't block the
        event loop — FastAPI golden rule "async def everywhere").

IMPORTANT FOR LEARNERS — schema vs. execution:
    `web_search` (the @tool object) serves only as the SCHEMA for the LLM
    (bind_tools). WE execute the search ourselves in the graph loop via
    `search_web()` — because we also want the raw sources (list[Source])
    in the state, not just the text for the ToolMessage.
"""

import asyncio
import logging
from typing import TypedDict

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


class Source(TypedDict):
    """A source from the web search — lands in the state and in the DB."""

    title: str
    url: str
    snippet: str


# ----------------------------------------------------------------------------
# Provider 1: DuckDuckGo (ddgs) — no key needed, hence the default.
# ----------------------------------------------------------------------------
async def _ddgs_search(query: str, max_results: int) -> list[Source]:
    def _sync() -> list[Source]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        try:
            raw = list(DDGS().text(query, max_results=max_results))
        except DDGSException as e:
            # "No results found" / rate limit are EXPECTED states here
            # (e.g. very specific German search phrases) — no stack trace,
            # just a quiet info. The agent continues with empty hits.
            logger.info("DuckDuckGo: keine Treffer/Rate-Limit (%s): %s", query[:60], e)
            return []
        return [
            Source(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
            for r in raw
            if r.get("href")
        ]

    # ddgs is synchronous -> thread pool so the event loop stays free.
    return await asyncio.to_thread(_sync)


# ----------------------------------------------------------------------------
# Provider 2: Tavily — active as soon as a key is configured.
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
# Provider selection (like get_llm(): key present -> Tavily, else ddgs)
# ----------------------------------------------------------------------------
async def search_web(query: str, max_results: int = MAX_RESULTS) -> list[Source]:
    """Searches the web and returns normalized sources (title/url/snippet)."""
    if settings.tavily_api_key:
        try:
            return await _tavily_search(query, max_results)
        except Exception:  # noqa: BLE001 — fallback instead of a crash
            logger.warning("Tavily-Suche fehlgeschlagen — Fallback auf DuckDuckGo", exc_info=True)
    try:
        return await _ddgs_search(query, max_results)
    except Exception:  # noqa: BLE001 — network down? Empty list, agent continues.
        logger.warning("DuckDuckGo-Suche fehlgeschlagen (query=%s)", query, exc_info=True)
        return []


def format_sources(sources: list[Source]) -> str:
    """Formats sources as text — this is how they go into the ToolMessage to the LLM."""
    if not sources:
        return "Keine Treffer."
    lines = [
        f"[{i + 1}] {s['title']}\n    URL: {s['url']}\n    {s['snippet'][:300]}"
        for i, s in enumerate(sources)
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# The SCHEMA (for bind_tools) — the graph loop does the execution itself.
# ----------------------------------------------------------------------------
@tool
async def web_search(query: str) -> str:
    """Durchsucht das Web nach aktuellen Informationen zu einer Frage.

    Liefert die Top-Treffer als nummerierte Liste mit Titel, URL und
    Kurzbeschreibung. Nutze mehrdeutige Begriffe nicht zu allgemein, sondern
    formuliere eine konkrete Suchanfrage.
    """
    # Only called if someone executes the tool directly (e.g. tests).
    return format_sources(await search_web(query))


# ----------------------------------------------------------------------------
# Normalization of the source list — THE ONE source of truth for citations
# ----------------------------------------------------------------------------
def normalize_sources(sources: list, cap: int = 60) -> list:
    """
    Deduplicates sources by URL (last sighting wins, order = position of
    the last occurrence) and caps at `cap`.

    IMPORTANT: SYNTHESIZER (numbering in the prompt) AND service
    (persisted source list) both use this function — only this way do
    [n] citations in the report and the UI's source list point to the
    SAME source. Two separate dedupe logics had the consequence that
    [7] on the UI pointed to the wrong URL or wasn't linkable at all.
    """
    seen: dict[str, dict] = {}
    for s in reversed(sources):
        url = s.get("url", "")
        if url and url not in seen:
            seen[url] = s
    # seen.values() is now "latest sighting first" -> reverse = oldest of
    # the last sightings first; then cap.
    return list(seen.values())[::-1][:cap]


# ----------------------------------------------------------------------------
# DOCUMENT SEARCH (Stage 5+): user uploads as a searchable agent tool
# ----------------------------------------------------------------------------
def make_document_search_tool(documents: list[dict]):
    """
    Builds a document_search tool over exactly THIS project's documents.

    Why a factory/closure? A tool needs access to the document texts,
    but @tool schemas are static — so we create the tool PER RUN with
    the current documents in the closure. Naive keyword search (window
    around hits) instead of embeddings: good enough, zero infrastructure.
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
            # Rough window search: the position with the most term hits
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
                    if best_score > len(terms) * 3:  # good enough
                        break
            if best_pos >= 0:
                snippet = text[max(0, best_pos - 150) : best_pos + 350].replace("\n", " ")
                results.append(f"[{name}] …{snippet}…")
        if not results:
            return "Keine Treffer in den Dokumenten."
        return "\n\n".join(results[:5])

    return document_search
