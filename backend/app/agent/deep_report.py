"""
agent/deep_report.py — the deep-report pipeline (Phase 2)
=======================================================

For questions with depth="deep" — true long-form reports (target: 30+ pages):

    OUTLINE PLANNER   → outline with 8–14 chapters (JSON-planned, visible
                        live in the chat as a TODO list)
    RESEARCH FAN-OUT  → 2 search queries per chapter, parallel researchers
                        (the same ReAct loop as in quick mode)
    REGISTRY          → all sources deduplicated & globally numbered [1..n]
    CHAPTER WRITERS   → SEQUENTIAL (one long text of 1500–2500 words per
                        chapter; cite [n] from the registry; write with an
                        eye on the previous chapters for a common thread)
    ASSEMBLY          → title page, abstract, table of contents, chapters,
                        bibliography (APA/IEEE, deterministic)

Why chapters sequentially? A 30-page report in ONE LLM call is impossible
(output limits!). Chapter by chapter, depth emerges + every call stays
within the token budget. The chapter writers get the sub-headings of
their predecessors as "common thread" context.
"""

import asyncio
import json
import logging
import operator
import re
from functools import partial
from typing import Annotated, TypedDict

from app.agent.citations import format_bibliography
from app.agent.events import emit
from app.agent.language import polish_german
from app.agent.llm import ainvoke_with_retry, get_llm
from app.agent.nodes.researcher import run_react_loop
from app.agent.tools import normalize_sources
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

MAX_CHAPTERS = 14

# Backpressure: max. 3 concurrent LLM calls in deep mode (DeepSeek rate
# limits!). IMPORTANT: this semaphore is actually used by the deep nodes
# (chapter_research + chapter_writer) — before, it was a dead line of
# code and the parallel researchers shot down the provider.
_deep_semaphore = asyncio.Semaphore(3)
MIN_CHAPTERS = 6
WORDS_PER_CHAPTER = (1500, 2500)


# ----------------------------------------------------------------------------
# STATE
# ----------------------------------------------------------------------------
class DeepReportState(TypedDict):
    question: str
    documents: list[dict]
    context_summary: str
    outline: dict | None  # {"title": …, "abstract": …, "chapters": [{title, focus}]}
    chapter_findings: Annotated[list[dict], operator.add]  # per chapter: {chapter, answer, sources}
    chapters_written: list[dict]  # [{title, content}] — filled sequentially (overwrite channel)
    usage: Annotated[list[dict], operator.add]
    citation_style: str  # "apa" | "ieee" | "plain"
    report: str | None


# ----------------------------------------------------------------------------
# 1) OUTLINE PLANNER
# ----------------------------------------------------------------------------
OUTLINE_PROMPT = """Du bist ein Wissenschaftlicher Outline-Planer. Erstelle eine Gliederung für
einen
tiefgehenden Fachbericht zur Frage unten.

Anforderungen:
- {min} bis {max} Kapitel, von Grundlagen zu fortgeschrittenen Aspekten bis Ausblick/Fazit
- Jedes Kapitel: klarer Titel + 1–2 Sätze Fokus-Beschreibung (was muss recherchiert werden?)
- Ein "Abstract" (3–4 Sätze, was der Bericht liefert)
- Ein Gesamttitel

Antworte AUSSCHLIESSLICH mit JSON:
{{"title": "…", "abstract": "…", "chapters": [{{"title": "…", "focus": "…"}}]}}

Frage: {question}{context}"""


def _parse_outline_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    chapters = [
        {
            "title": str(c.get("title", "")).strip(),
            "focus": str(c.get("focus", "")).strip(),
        }
        for c in data.get("chapters", [])
        if str(c.get("title", "")).strip()
    ]
    if len(chapters) < 3:
        return None
    data["chapters"] = chapters[:MAX_CHAPTERS]
    data["title"] = str(data.get("title", "")).strip() or "Fachbericht"
    data["abstract"] = str(data.get("abstract", "")).strip()
    return data


async def outline_node(state: DeepReportState, llm=None) -> dict:
    """Plans the outline — the TODO list the user sees live."""
    emit("node", node="outline", status="start")
    model = llm or get_llm()
    question = state["question"]

    # RESUME/re-run: if an outline already sits in the (checkpoint) state,
    # it is NOT replanned — 0 tokens and the chapter titles stay stable, so
    # the writer can reliably skip already-written chapters.
    if state.get("outline"):
        emit("outline", outline=state["outline"])
        emit("node", node="outline", status="end", chapters=len(state["outline"]["chapters"]))
        return {"usage": []}

    if model is None:  # echo mode (tests/without a key)
        outline = {
            "title": f"Fachbericht: {question}",
            "abstract": "Echo-Gliederung.",
            "chapters": [
                {"title": "Einleitung", "focus": question},
                {"title": "Grundlagen", "focus": question},
                {"title": "Fazit", "focus": question},
            ],
        }
        emit("outline", outline=outline)
        emit("node", node="outline", status="end", chapters=len(outline["chapters"]))
        return {"outline": outline, "usage": []}

    context = ""
    if state.get("context_summary"):
        context = f"\n\nBisheriger Gesprächsverlauf:\n{state['context_summary']}"

    response = await ainvoke_with_retry(
        model,
        OUTLINE_PROMPT.format(
            min=MIN_CHAPTERS,
            max=MAX_CHAPTERS,
            question=question,
            context=context,
        ),
    )
    outline = _parse_outline_json(str(response.content))
    if outline is None:  # fallback: 6 standard chapters
        outline = {
            "title": f"Fachbericht: {question[:80]}",
            "abstract": "",
            "chapters": [
                {"title": t, "focus": question}
                for t in (
                    "Einleitung und Motivation",
                    "Grundlagen und Definitionen",
                    "Stand der Forschung und Praxis",
                    "Kernanalyse",
                    "Kritische Diskussion",
                    "Fazit und Ausblick",
                )
            ],
        }
    emit("outline", outline=outline)
    emit("node", node="outline", status="end", chapters=len(outline["chapters"]))

    # NO more interrupt() (user decision): the outline is displayed as a
    # TODO list in the chat, but the pipeline continues DIRECTLY — no
    # confirmation needed, no second connection, no state loss on reloads
    # or rate limits. Just do it. 🦊

    return {"outline": outline, "usage": [extract_usage(response)]}


# ----------------------------------------------------------------------------
# 2) CHAPTER RESEARCH (ReAct loop per chapter, parallel via Send in the graph)
# ----------------------------------------------------------------------------
async def chapter_research_node(state: dict, llm=None) -> dict:
    """Researches ONE chapter (input arrives via Send)."""
    chapter_title = state["chapter_title"]
    focus = state.get("chapter_focus", chapter_title)

    # RESUME: a finding from an earlier (aborted) run already sits in the
    # state for this chapter — don't research it again (0 tokens).
    if state.get("already_researched"):
        return {"chapter_findings": [], "usage": []}

    model = llm or get_llm()
    emit("node", node="researcher", status="start", sub_question=chapter_title)
    emit(
        "tool",
        tool="research_chapter",
        query=f"Kapitel: {chapter_title[:50]}",
        agent="chapter_research",
    )

    if model is None:
        return {
            "chapter_findings": [
                {"chapter": chapter_title, "answer": "[Echo-Modus]", "sources": []}
            ],
            "usage": [],
        }

    async with _deep_semaphore:
        answer, sources, usage = await run_react_loop(model, f"{chapter_title}: {focus}", None)
    emit(
        "node",
        node="researcher",
        status="end",
        sub_question=chapter_title,
        sources=len(sources),
    )
    return {
        "chapter_findings": [
            {"chapter": chapter_title, "answer": answer, "sources": list(sources)}
        ],
        "usage": usage,
    }


def clean_chapter_content(content: str, chapter_title: str) -> str:
    """
    Removes a redundant chapter heading the LLM may have prefixed to the
    content, so it doesn't appear twice after '## {i}. {title}'.
    """
    if not content or not content.strip():
        return ""
    lines = content.strip().split("\n")
    if not lines:
        return ""

    first_line = lines[0].strip()

    cleaned_first = re.sub(r"^[#\s\*_`]+", "", first_line)
    cleaned_first = re.sub(r"^(?:Kapitel\s*)?\d+(?!\.\d)[\.\:\)\-–—\s]+", "", cleaned_first)
    cleaned_first = re.sub(r"[#\s\*_`]+$", "", cleaned_first).strip()

    clean_title = re.sub(r"^(?:Kapitel\s*)?\d+(?!\.\d)[\.\:\)\-–—\s]+", "", chapter_title).strip()

    def _normalize(s: str) -> str:
        return re.sub(r"[^\w\s]", "", s).lower().strip()

    norm_first = _normalize(cleaned_first)
    norm_title = _normalize(clean_title)

    if (
        norm_first
        and norm_title
        and (
            norm_first == norm_title
            or norm_first.startswith(norm_title)
            or norm_title.startswith(norm_first)
        )
    ):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
        return "\n".join(lines).strip()

    return content.strip()


# ----------------------------------------------------------------------------
# 3) CHAPTER WRITERS (sequential — the common thread)
# ----------------------------------------------------------------------------
WRITER_PROMPT = """Du bist ein wissenschaftlicher Fachautor. Schreibe das Kapitel "{chapter}" für
den Bericht "{title}".

Rechercheergebnisse für dieses Kapitel:
{findings}

{thread}

WICHTIGE REGELN:
- {min_words}–{max_words} Wörter, gut strukturiert mit 2–4 Zwischenüberschriften (###)
- Beginne DIREKT mit der Einleitung des Kapitels oder der ersten Zwischenüberschrift (###);
  wiederhole den Haupttitel des Kapitels NICHT am Anfang als Überschrift
- Zitiere mit [n] aus der NUMMERIERTEN Quellenliste unten
- Sachlich-wissenschaftlicher Stil, keine Füllphrasen, konkrete Fakten und Zahlen
- Schreibe auf Deutsch — AUSSCHLIESSLICH deutsche Schrift: keine chinesischen,
  kyrillischen oder anderen fremden Schriftzeichen
- Keine erfundenen Schein-Anglizismen oder Denglisch-Wortschöpfungen (z. B.
  "Vergütungsmodelle" statt "Compensation-Modelle", "Gehaltsbenchmarks" /
  "Gehaltsvergleich" statt "Compensation-Benchmarks") — verwende präzises,
  idiomatisch korrektes Fachdeutsch
- KEINE Zusammenfassung am Ende des Kapitels (das macht das Gesamtfazit)

Nummerierte Quellen (zitieren als [n]):
{sources}

Fragen-/Berichtskontext: {question}"""


async def chapter_writer_node(state: DeepReportState, llm=None) -> dict:
    """Writes all chapters sequentially — each with common-thread context.

    RESUME: already-written chapters (state["chapters_written"]) are
    skipped instead of rewritten.
    """
    emit(
        "node",
        node="writer",
        status="start",
        chapters=len((state.get("outline") or {}).get("chapters", [])),
    )
    model = llm or get_llm()
    outline = state["outline"] or {"title": "Bericht", "chapters": []}
    findings_by_chapter = {f["chapter"]: f for f in state.get("chapter_findings", [])}
    all_sources = normalize_sources(
        [s for f in state.get("chapter_findings", []) for s in f.get("sources", [])],
        cap=100,
    )
    sources_listing = "\n".join(
        f"[{i + 1}] {s.get('title', '')[:80]} — {s.get('url', '')[:80]}"
        for i, s in enumerate(all_sources)
    )

    # RESUME: take over already-written chapters (title -> content).
    written: list[dict] = list(state.get("chapters_written") or [])
    written_titles = {w["title"] for w in written}
    usage: list[dict] = []
    if model is None:  # Echo
        for ch in outline["chapters"]:
            if ch["title"] in written_titles:
                continue
            written.append({"title": ch["title"], "content": f"### {ch['title']}\n\n[Echo-Modus]"})
        return {
            "chapters_written": written,
            "report": _assemble(outline, written, all_sources, state.get("citation_style", "ieee")),
            "usage": [],
        }

    for index, chapter in enumerate(outline["chapters"], start=1):
        if chapter["title"] in written_titles:
            continue  # already written (resume) — don't write again
        emit(
            "chapter",
            status="start",
            index=index,
            total=len(outline["chapters"]),
            title=chapter["title"],
        )
        finding = findings_by_chapter.get(chapter["title"], {})
        thread = ""
        if written:
            thread = (
                "Bisherige Kapitel (behalte den roten Faden, wiederhole dich nicht):\n"
                + "\n".join(f"- {w['title']}" for w in written[-4:])
            )
        prompt = WRITER_PROMPT.format(
            chapter=chapter["title"],
            title=outline["title"],
            findings=(
                finding.get("answer")
                or "Keine Rechercheergebnisse — nutze dein Wissen und kennzeichne das."
            )[:6000],
            thread=thread,
            min_words=WORDS_PER_CHAPTER[0],
            max_words=WORDS_PER_CHAPTER[1],
            sources=sources_listing or "(keine Quellen)",
            question=state["question"],
        )
        chapter_ctx = f"Kapitel {index}: {chapter['title'][:40]}"
        emit("tool", tool="write_chapter", query=chapter_ctx, agent="writer")
        async with _deep_semaphore:
            response = await ainvoke_with_retry(model, prompt, context=chapter_ctx)
        usage.append(extract_usage(response))
        content = str(response.content).strip()
        # Language lector: foreign script characters (free regex) +
        # pseudo-Anglicisms/grammar (mini YES/NO check). The correction runs
        # only when needed — clean chapters cost NO extra tokens.
        content, polish_usage = await polish_german(model, content, chapter_ctx)
        usage.extend(polish_usage)
        content = clean_chapter_content(content, chapter["title"])
        written.append({"title": chapter["title"], "content": content})
        written_titles.add(chapter["title"])
        # Live progress: a finished chapter goes straight into the stream +
        # report. The content is sent along so the service persists it
        # IMMEDIATELY (crash-safe: finished chapters are never lost).
        emit(
            "chapter",
            status="done",
            index=index,
            total=len(outline["chapters"]),
            title=chapter["title"],
            words=len(content.split()),
            content=content,
        )

    report = _assemble(outline, written, all_sources, state.get("citation_style", "ieee"))
    emit("node", node="writer", status="end")
    return {"chapters_written": written, "report": report, "usage": usage}


def _assemble(outline: dict, chapters: list[dict], sources: list, style: str) -> str:
    """Assembles the final report (title page, abstract, TOC, chapters, bibliography)."""
    parts: list[str] = [f"# {outline.get('title', 'Fachbericht')}\n"]
    if outline.get("abstract"):
        parts.append(f"> **Abstract:** {outline['abstract']}\n")
    # Table of contents
    parts.append("## Inhaltsverzeichnis\n")
    for i, ch in enumerate(chapters, start=1):
        parts.append(f"{i}. {ch['title']}")
    parts.append("")
    for i, ch in enumerate(chapters, start=1):
        clean_content = clean_chapter_content(ch.get("content", ""), ch.get("title", ""))
        parts.append(f"## {i}. {ch['title']}\n\n{clean_content}\n")
    parts.append(format_bibliography(sources, style))
    return "\n".join(parts)


# ----------------------------------------------------------------------------
# 4) GRAPH BUILDER (outline → parallel chapter research → writer → end)
# ----------------------------------------------------------------------------
def build_deep_report_graph(checkpointer=None, llm=None):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send

    def fan_out_chapters(state: DeepReportState) -> list[Send]:
        outline = state.get("outline") or {"chapters": []}
        # RESUME: chapters that already have a finding (from the checkpoint
        # state of an aborted run) are skipped via a flag — the Send fires,
        # but calls no LLM (no double costs).
        researched = {f["chapter"] for f in state.get("chapter_findings", []) if f.get("chapter")}
        return [
            Send(
                "chapter_research",
                {
                    "chapter_title": ch["title"],
                    "chapter_focus": ch.get("focus", ""),
                    "documents": state.get("documents", []),
                    "already_researched": ch["title"] in researched,
                },
            )
            for ch in outline.get("chapters", [])
        ]

    builder = StateGraph(DeepReportState)
    builder.add_node("outline", partial(outline_node, llm=llm))
    builder.add_node("chapter_research", partial(chapter_research_node, llm=llm))
    builder.add_node("writer", partial(chapter_writer_node, llm=llm))
    builder.add_edge(START, "outline")
    builder.add_conditional_edges("outline", fan_out_chapters, ["chapter_research"])
    builder.add_edge("chapter_research", "writer")
    builder.add_edge("writer", END)
    return builder.compile(checkpointer=checkpointer)
