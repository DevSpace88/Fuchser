"""
nodes/researcher.py — THE RESEARCHER (Stage 3: once per sub-question)
======================================================================

The heart of it: the ReAct loop from Stage 2, now as a node that is
INSTANTIATED multiple times PER `Send` — each instance with its own
sub-question.

    Send("researcher", {"sub_question": "...", "question": "..."})

For this to work without parallel instances overwriting each other, we
only write into REDUCER channels (findings/sources with operator.add):
each instance delivers ITS contribution, LangGraph merges them in the
join (superstep semantics).

The semaphore (PLAN.md §9) throttles parallel LLM calls against the
provider's rate limits.
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

# Safety net against endless searching (see run_react_loop).
MAX_TOOL_ROUNDS = 3

# Max. concurrently running LLM calls among the parallel researchers.
_LLM_SEMAPHORE = asyncio.Semaphore(4)


class Finding(TypedDict):
    """The result of ONE researcher — building block for the synthesizer."""

    sub_question: str
    answer: str


class ResearcherInput(TypedDict):
    """Input state of a researcher instance (arrives in the Send object)."""

    question: str
    sub_question: str
    documents: list[dict]  # uploaded documents (document_search tool)


async def run_react_loop(
    llm: BaseChatModel,
    query: str,
    extra_tools: list | None = None,
) -> tuple[str, list[Source], list[dict]]:
    """
    The hand-built ReAct loop (Stage 2 — proven, unchanged):

        LLM + tool schema -> tool_calls? -> run the search ->
        hits back as a ToolMessage -> repeat -> final answer.

    Returns (answer text, collected sources, usage list).
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

    # bind_tools only attaches the tools' JSON schemas to the request —
    # WE execute them ourselves in the loop below. extra_tools: e.g.
    # document_search (only if the user uploaded documents).
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
            # No more tool requests -> this is the final answer.
            return str(response.content), collected_sources, usage

        logger.info("Recherche-Runde %d: %d Tool-Call(s)", round_no, len(response.tool_calls))
        for call in response.tool_calls:
            query_arg = call.get("args", {}).get("query", "")
            emit("tool", tool=call.get("name", "?"), query=query_arg[:80], agent="researcher")
            if call.get("name") == "document_search":
                # Search a user document: result as context (not as a
                # web-based source — there is no URL to link to).
                tool_obj = next(t for t in tools if getattr(t, "name", "") == "document_search")
                doc_hits = await tool_obj.ainvoke({"query": query_arg})
                messages.append(
                    ToolMessage(content=str(doc_hits), tool_call_id=call.get("id", ""))
                )
                continue
            results = await search_web(query_arg)  # actual execution
            collected_sources.extend(results)
            # Hand the hits back to the LLM as a ToolMessage (referenced via
            # tool_call_id — that's how the model knows which call this belongs to).
            messages.append(
                ToolMessage(
                    content=format_sources(results),
                    tool_call_id=call.get("id", ""),
                )
            )

    # Cap reached: force a final answer without tools.
    logger.info("Max. Tool-Runden erreicht — finalisiere ohne weitere Suche.")
    async with _LLM_SEMAPHORE:
        final = await llm.ainvoke(messages)
    usage.append(extract_usage(final))
    return str(final.content), collected_sources, usage


async def researcher_node(state: ResearcherInput, llm: BaseChatModel | None = None) -> dict:
    """
    One researcher instance for ONE sub-question.

    Returns ONLY into reducer channels: {'findings': [...], 'sources': [...]}
    — precisely why any number of instances can run in parallel.
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
