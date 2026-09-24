"""
agent/deep_report.py — Deep-Report-Pipeline (Phase 2)
=======================================================

Für Fragen mit depth="deep" — echte Langform-Berichte (Ziel: 30+ Seiten):

    OUTLINE-PLANER  → Gliederung mit 8–14 Kapiteln (JSON-geplant, als
                      TODO-Liste live im Chat sichtbar)
    RESEARCH-FAN-OUT → pro Kapitel 2 Suchfragen, parallele Researcher
                      (derselbe ReAct-Loop wie im Quick-Modus)
    REGISTRY        → alle Quellen dedupliziert & global nummeriert [1..n]
    KAPITEL-AUTOREN → SEQUENZIELL (je Kapitel ein langer Text von 1500–
                      2500 Wörtern; zitieren [n] aus der Registry; schreiben
                      mit Blick auf die bisherigen Kapitel für Roten Faden)
    ASSEMBLY        → Titelblatt, Abstract, Inhaltsverzeichnis, Kapitel,
                      Literaturverzeichnis (APA/IEEE, deterministisch)

Warum Kapitel sequenziell? Ein 30-Seiten-Report in EINEM LLM-Call ist
unmöglich (Output-Limits!). Kapitelweise entsteht Tiefe + jeder Call
bleibt im Token-Budget. Die Kapitel-Autoren bekommen die Zwischenüberschriften
der Vorgänger als "roter Faden"-Kontext.
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

# Backpressure: max. 3 gleichzeitige LLM-Calls im Deep-Modus
# (DeepSeek-Rate-Limits!). WICHTIG: dieser Semaphore wird von den Deep-Nodes
# tatsächlich benutzt (chapter_research + chapter_writer) — vorher war er tote
# Zeile und die parallelen Researcher haben den Provider zugeschossen.
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
    chapter_findings: Annotated[list[dict], operator.add]  # pro Kapitel: {chapter, answer, sources}
    chapters_written: list[dict]  # [{title, content}] — wird sequenziell gefüllt (overwrite-Kanal)
    usage: Annotated[list[dict], operator.add]
    citation_style: str  # "apa" | "ieee" | "plain"
    report: str | None


# ----------------------------------------------------------------------------
# 1) OUTLINE-PLANER
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
    """Plant die Gliederung — die TODO-Liste, die der User live sieht."""
    emit("node", node="outline", status="start")
    model = llm or get_llm()
    question = state["question"]

    # RESUME/Re-Run: Steht eine Gliederung im (Checkpoint-)State, wird sie
    # NICHT neu geplant — 0 Token und die Kapitel-Titel bleiben stabil, damit
    # der Writer bereits geschriebene Kapitel zuverlässig überspringen kann.
    if state.get("outline"):
        emit("outline", outline=state["outline"])
        emit("node", node="outline", status="end", chapters=len(state["outline"]["chapters"]))
        return {"usage": []}

    if model is None:  # Echo-Modus (Tests/ohne Key)
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
    if outline is None:  # Fallback: 6 Standard-Kapitel
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

    # KEIN interrupt() mehr (User-Entscheidung): Die Gliederung wird als
    # TODO-Liste im Chat angezeigt, aber die Pipeline läuft DIREKT weiter
    # — keine Bestätigung nötig, keine zweite Verbindung, kein State-
    # Verlust bei Reloads oder Rate-Limits. Einfach machen. 🦊

    return {"outline": outline, "usage": [extract_usage(response)]}


# ----------------------------------------------------------------------------
# 2) KAPITEL-RECHERCHE (pro Kapitel ReAct-Loop, parallel via Send im Graphen)
# ----------------------------------------------------------------------------
async def chapter_research_node(state: dict, llm=None) -> dict:
    """Recherchiert EIN Kapitel (Input kommt per Send mit)."""
    chapter_title = state["chapter_title"]
    focus = state.get("chapter_focus", chapter_title)

    # RESUME: Für dieses Kapitel liegt bereits ein Finding aus einem früheren
    # (abgebrochenen) Lauf im State — nicht erneut recherchieren (0 Token).
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
    Entfernt eine eventuell vom LLM vorangestellte redundante Kapitelüberschrift
    am Anfang des Inhalts, damit sie nach '## {i}. {title}' nicht doppelt erscheint.
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
# 3) KAPITEL-AUTOREN (sequenziell — der rote Faden)
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
    """Schreibt alle Kapitel sequenziell — jedes mit Rotem-Faden-Kontext.

    RESUME: bereits geschriebene Kapitel (state["chapters_written"]) werden
    übersprungen statt neu geschrieben.
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

    # RESUME: bereits geschriebene Kapitel übernehmen (Titel -> Inhalt).
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
            continue  # bereits geschrieben (Resume) — nicht erneut schreiben
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
        # Sprach-Lektor: fremde Schriftzeichen (Gratis-Regex) + Schein-
        # Anglizismen/Grammatik (Mini-YES/NO-Check). Die Korrektur läuft nur
        # bei Bedarf — saubere Kapitel kosten KEINE zusätzlichen Tokens.
        content, polish_usage = await polish_german(model, content, chapter_ctx)
        usage.extend(polish_usage)
        content = clean_chapter_content(content, chapter["title"])
        written.append({"title": chapter["title"], "content": content})
        written_titles.add(chapter["title"])
        # Live-Fortschritt: fertiges Kapitel sofort in den Stream + Report.
        # Der Inhalt wird mitgeschickt, damit der Service ihn SOFORT persistiert
        # (Crash-sicher: fertige Kapitel gehen nie verloren).
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
    """Setzt den finalen Bericht zusammen (Titelblatt, Abstract, TOC, Kapitel, Literatur)."""
    parts: list[str] = [f"# {outline.get('title', 'Fachbericht')}\n"]
    if outline.get("abstract"):
        parts.append(f"> **Abstract:** {outline['abstract']}\n")
    # Inhaltsverzeichnis
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
# 4) GRAPH-BUILDER (Outline → parallele Kapitel-Recherche → Writer → Ende)
# ----------------------------------------------------------------------------
def build_deep_report_graph(checkpointer=None, llm=None):
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send

    def fan_out_chapters(state: DeepReportState) -> list[Send]:
        outline = state.get("outline") or {"chapters": []}
        # RESUME: Kapitel mit bereits vorhandenem Finding (aus dem Checkpoint-
        # State eines abgebrochenen Laufs) werden per Flag übersprungen —
        # der Send läuft an, ruft aber kein LLM (keine doppelten Kosten).
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
