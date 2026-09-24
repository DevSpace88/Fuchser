"""
services/research_service.py — GESCHÄFTSLOGIK für Recherche-Projekte
====================================================================

Zwei Verantwortungen:
    1) CRUD auf research_projects — IMMER gefiltert nach user_id
       (Ownership: fremde Projekte sind unsichtbar → ProjectNotFound).
    2) Den LangGraph-Lauf als SSE-Event-Strom: run_research_stream()
       kapselt astream() und übersetzt LangGraph-Chunks in unser
       SSE-Event-Protokoll (siehe PLAN.md §4).
    3) report_to_pdf(): Report + Quellen als echtes PDF (Download, nicht
       Browser-Druck) — Markdown → HTML → xhtml2pdf.

SSE-Protokoll (Stufe 4):
    status             {"status": "running"}
    node               {"node": "supervisor", "status": "start"|"end", …}
                       Live-Telemetrie für die Graph-Visualisierung (custom-
                       Stream-Events aus app/agent/events.py)
    subagents_planned  {"sub_questions": [...]}          Supervisor fertig
    subagent           {"sub_question": ..., "sources": n}  Researcher fertig
    token              {"text": "..."}                   Synthesizer live
    sources            {"sources": [...]}                deduplizierte Quellen
    done / error

Der Service importiert bewusst KEIN FastAPI — er ist ohne HTTP testbar.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.agent.graph import build_research_graph
from app.agent.persistence import get_checkpointer
from app.agent.usage import sum_usage
from app.models.research_project import ResearchProject, ResearchStatus
from app.models.user import User
from app.schemas.research import ResearchCreate

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Fehler-Typen: der Endpunkt übersetzt sie in saubere HTTP-Codes (404/409).
# ----------------------------------------------------------------------------
class ProjectNotFound(Exception):
    """Projekt existiert nicht — ODER gehört einem anderen User."""


# ============================================================================
# 1) CRUD (immer ownership-gefiltert)
# ============================================================================
async def create_project(
    session: AsyncSession, user: User, data: ResearchCreate
) -> ResearchProject:
    """
    Legt ein neues Recherche-Projekt für `user` an (Status: queued).

    Stufe 5 — Follow-ups: Ist `followup_of` gesetzt (und gehört dem User),
    übernimmt das Projekt den THREAD der Ursprungs-Recherche: Der Graph
    läuft dann im selben Kontext und der Supervisor sieht die bisherigen
    findings (plant nur neue Aspekte). Fremde parent-IDs → ProjectNotFound.
    """
    parent = None
    if data.followup_of is not None:
        # wirft ProjectNotFound bei fremden/unbekannten IDs -> 404
        parent = await get_project(session, user, data.followup_of)

    # ---- Unterhaltung bestimmen (Google-AI-Studio-Modell) ----
    from app.models.conversation import Conversation
    from app.services.conversation_service import create_conversation

    conversation_id = data.conversation_id
    if conversation_id is not None:
        conv = await session.get(Conversation, conversation_id)
        if conv is None or conv.user_id != user.id:
            raise ProjectNotFound(str(conversation_id))
    elif parent is not None and parent.conversation_id is not None:
        # Nachfrage -> gehört in die Unterhaltung des Parents
        conversation_id = parent.conversation_id
    elif parent is not None:
        # Alt-Daten: Parent hat noch keine Conversation -> für ihn anlegen
        conv = await create_conversation(session, user, parent.question[:200])
        parent.conversation_id = conv.id
        session.add(parent)
        conversation_id = conv.id
    else:
        # Neue eigenständige Frage -> neue Unterhaltung (Titel = Frage)
        conv = await create_conversation(session, user, data.question[:200])
        conversation_id = conv.id

    project = ResearchProject(
        user_id=user.id,
        question=data.question,
        status=ResearchStatus.QUEUED.value,
        parent_id=parent.id if parent else None,
        thread_id=parent.thread_id if parent else uuid.uuid4(),
        context_summary=data.context_summary,
        conversation_id=conversation_id,
        depth=data.depth,
        citation_style=data.citation_style,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def list_projects(session: AsyncSession, user: User) -> list[ResearchProject]:
    """Alle Projekte des Users, neueste zuerst."""
    result = await session.exec(
        select(ResearchProject)
        .where(ResearchProject.user_id == user.id)
        .order_by(ResearchProject.created_at.desc())
    )
    return list(result.all())


async def get_project(session: AsyncSession, user: User, project_id: UUID) -> ResearchProject:
    """
    Einzelnes Projekt — wirft ProjectNotFound, wenn es das Projekt nicht gibt
    ODER es einem anderen User gehört (nach außen identisch: 404).
    """
    project = await session.get(ResearchProject, project_id)
    if project is None or project.user_id != user.id:
        raise ProjectNotFound(str(project_id))
    return project


async def rename_project(
    session: AsyncSession, user: User, project_id: UUID, title: str
) -> ResearchProject:
    """Setzt den Anzeigename (PDF-Titel, Listen). Die Frage bleibt gleich."""
    project = await get_project(session, user, project_id)
    project.title = title.strip()
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def delete_project(session: AsyncSession, user: User, project_id: UUID) -> None:
    """Löscht ein Projekt (Checkpoints bleiben zunächst bestehen — aufräumen kommt in Stufe 5)."""
    project = await get_project(session, user, project_id)
    await session.delete(project)
    await session.commit()


# ============================================================================
# 2) DER GRAPH-LAUF ALS SSE-STROM (Stufe 3: Multi-Agent)
# ============================================================================
def _sse(event: str, data: dict) -> str:
    """Formatiert ein Event als Server-Sent-Events-Frame (`event: X\ndata: {...}\n\n`)."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _dedupe_sources(sources: list[dict]) -> list[dict]:
    """Quellen normieren — GLEICHE Logik wie im Synthesizer (tools.
    normalize_sources), damit [n]-Zitate im Report mit der UI-Liste
    übereinstimmen (gleiche Reihenfolge, gleicher Cap)."""
    from app.agent.tools import normalize_sources

    return normalize_sources(sources)


# Phase je Node für die Status-Anzeige (Stufe 4: phasensynchron).
_NODE_PHASE = {
    "supervisor": "planning",
    "researcher": "researching",
    "synthesizer": "synthesizing",
    "critic": "reviewing",
}


def _redis_run_action(status: str, marker_exists: bool) -> str:
    """Reconnect-Entscheidung für den Redis-Pfad: 'subscribe' oder 'push'.

    'subscribe': Task ist bereits aktiv (Queue ODER Worker — per Run-Marker
                 belegt). Nur zuhören, NIEMALS ein zweites Mal einreihen.
    'push':      Kein aktiver Task -> einreihen. Deckt auch den Fall ab, in
                 dem der Status 'running' ist, der Worker aber gestorben ist
                 (Marker verfallen) -> Neu-Anstoß mit günstigem Resume.
    """
    if marker_exists and status in (
        ResearchStatus.RUNNING.value,
        ResearchStatus.QUEUED.value,
    ):
        return "subscribe"
    return "push"


async def run_research_stream(
    session: AsyncSession,
    project: ResearchProject,
) -> AsyncIterator[str]:
    """
    REDIS-ARCHITEKTUR: Der Graph läuft im WORKER (eigener Prozess),
    NICHT hier im SSE-Request. Browser-Reloads töten den Agenten nicht mehr.

    1. Task in Redis-Queue pushen (Worker nimmt ihn entgegen)
    2. SSE-Stream monitored Redis PubSub für Fortschritts-Events
    3. Wenn SSE disconnectet: Worker läuft WEITER
    """
    # Redis verfügbar? → Worker-Architektur (reload-sicher)
    # Redis nicht verfügbar? → Inline-Fallback (Tests, lokale Entwicklung)
    redis_available = False
    try:
        from app.core.redis import get_redis

        r = await get_redis()
        await r.ping()
        redis_available = True
    except Exception:  # noqa: BLE001 — Redis down → inline
        logger.info("Redis nicht verfügbar — Inline-Ausführung (kein Worker)")

    if redis_available:
        from app.core.redis import has_run_marker, push_task, set_run_marker, subscribe_progress

        pid = str(project.id)
        marker = await has_run_marker(pid)

        if _redis_run_action(project.status, marker) == "subscribe":
            # RECONNECT (z. B. Seiten-Reload): Der Task läuft bereits in
            # Queue/Worker — NUR abonnieren, keinen zweiten Task pushen.
            # (Vorher wurde hier blind gepusht => der komplette Lauf lief
            # nach dem Reload EIN ZWEITES MAL mit vollen Token-Kosten!)
            logger.info("Reconnect an laufenden Task %s — kein neuer Queue-Eintrag", pid)
        else:
            if project.status == ResearchStatus.RUNNING.value:
                # Status "running", aber kein aktiver Worker (Prozess gestorben):
                # Teilerfolg (chapters) bleibt erhalten -> günstiges Resume.
                logger.warning("Verwaister Lauf %s (Worker nicht mehr aktiv) — neu einreihen", pid)
            project.status = ResearchStatus.RUNNING.value
            session.add(project)
            await session.commit()
            await set_run_marker(pid)
            await push_task(pid)

        yield _sse("status", {"status": "running"})

        # Terminal-Race: Der Lauf kann genau jetzt enden (PubSub-Events, die
        # VOR unserem Subscribe publiziert wurden, sind verloren). Status
        # frisch aus der DB holen, bevor wir auf Events warten.
        await session.refresh(project)
        if project.status == ResearchStatus.DONE.value:
            yield _sse("done", {"status": "done"})
            return
        if project.status == ResearchStatus.ERROR.value:
            yield _sse("error", {"detail": project.error or "Lauf fehlgeschlagen"})
            return

        async for event_data in subscribe_progress(pid, idle_timeout=20.0):
            if event_data.get("event") == "__idle__":
                # 20s nichts gehört: Ist der Lauf noch alive? (Verpasstes
                # done/error ODER toter Worker — statt ewig blind warten.)
                await session.refresh(project)
                if project.status == ResearchStatus.DONE.value:
                    yield _sse("done", {"status": "done"})
                    return
                if project.status == ResearchStatus.ERROR.value:
                    yield _sse("error", {"detail": project.error or "Lauf fehlgeschlagen"})
                    return
                if not await has_run_marker(pid):
                    # Worker gestorben: Teilerfolg bleibt in der DB -> der
                    # User kann über "Fortsetzen" ab dem letzten Kapitel
                    # weitermachen statt von vorne.
                    project.status = ResearchStatus.ERROR.value
                    project.error = "Worker nicht mehr erreichbar — Lauf unterbrochen"
                    session.add(project)
                    await session.commit()
                    yield _sse("error", {"detail": project.error})
                    return
                # Keep-Alive: Browser/Proxy vor Inaktivitäts-Timeout (nach ~60s) schützen
                yield _sse("ping", {"status": "running"})
                continue
            event = event_data.pop("event", "node")
            yield _sse(event, event_data)
            if event in ("done", "error"):
                break
        return

    # --- INLINE-FALLBACK (wie vorher, für Tests / ohne Redis) ---
    yield _sse("status", {"status": "running"})

    # RESUME-Semantik: Teilkapitel vorhanden, aber kein fertiger Report ->
    # abgebrochenen Lauf fortsetzen (gleicher Thread!). Der Graph startet mit
    # Input, aber die Nodes sind IDEMPOTENT: Outline + Recherche überspringen
    # im Checkpoint-State vorhandenes, der Writer überspringt die gespeicherten
    # Kapitel => nur fehlende Kapitel kosten Tokens.
    # Sonst frischer Lauf (neuer Thread für Top-Level-Projekte, Kapitel reset).
    resuming = bool(project.chapters) and not project.report
    if not resuming:
        if project.parent_id is None:
            project.thread_id = uuid.uuid4()
        project.chapters = None
    project.status = ResearchStatus.RUNNING.value
    session.add(project)
    await session.commit()

    if project.depth == "deep":
        from app.agent.deep_report import build_deep_report_graph

        graph = build_deep_report_graph(checkpointer=get_checkpointer())
    else:
        graph = build_research_graph(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": str(project.thread_id)}}

    from app.services.document_service import (
        documents_for_agent,
        list_documents_for_chain,
    )

    docs = documents_for_agent(await list_documents_for_chain(session, project))

    if resuming:
        graph_input = {
            "question": project.question,
            "context_summary": project.context_summary or "",
            "documents": docs,
            "citation_style": project.citation_style or "ieee",
            "chapters_written": list(project.chapters or []),
        }
    else:
        graph_input = {
            "question": project.question,
            "context_summary": project.context_summary or "",
            "documents": docs,
            "citation_style": project.citation_style or "ieee",
            "chapters_written": [],
        }

    collected_tokens: list[str] = []
    all_sources: list[dict] = []
    usage_entries: list[dict] = []
    trace: list[dict] = []
    report: str | None = None
    last_phase: str | None = None
    last_trace_save = 0

    def record(event: str, data: dict) -> None:
        if len(trace) > 500:
            return
        entry = {"t": datetime.now(UTC).isoformat(), "event": event}
        for key in (
            "node",
            "status",
            "verdict",
            "gaps",
            "revision",
            "sub_question",
            "planned",
            "index",
            "title",
            "words",
        ):
            if key in data:
                entry[key] = data[key]
        trace.append(entry)

    async def save_trace() -> None:
        nonlocal last_trace_save
        if len(trace) - last_trace_save < 3:
            return
        last_trace_save = len(trace)
        project.trace = list(trace)
        session.add(project)
        await session.commit()

    try:
        async for mode, payload in graph.astream(
            graph_input,
            config,
            stream_mode=["custom", "messages", "updates"],
        ):
            if mode == "custom":
                yield _sse(payload.get("event", "node"), payload)
                record(payload.get("event", "node"), payload)
                await save_trace()
                if payload.get("event") == "outline" and payload.get("outline"):
                    project.outline = payload["outline"]
                    session.add(project)
                    await session.commit()
                elif (
                    payload.get("event") == "chapter"
                    and payload.get("status") == "done"
                    and payload.get("content")
                ):
                    # Kapitel SOFORT persistieren (Crash-sicher bei Abbruch).
                    chapters = list(project.chapters or [])
                    chapters.append(
                        {"title": payload.get("title", ""), "content": payload["content"]}
                    )
                    project.chapters = chapters
                    session.add(project)
                    await session.commit()
                phase = _NODE_PHASE.get(payload.get("node", ""))
                if phase and phase != last_phase:
                    last_phase = phase
                    record("status", {"status": phase})
                    yield _sse("status", {"status": phase})
                if payload.get("node") == "synthesizer" and payload.get("status") == "start":
                    collected_tokens.clear()
                    yield _sse("report_reset", {})
            elif mode == "messages":
                chunk, metadata = payload if isinstance(payload, tuple) else (payload, {})
                node = metadata.get("langgraph_node", "") if isinstance(metadata, dict) else ""
                text = getattr(chunk, "content", "")
                if node == "synthesizer" and isinstance(text, str) and text:
                    collected_tokens.append(text)
                    yield _sse("token", {"text": text})
            elif mode == "updates":
                for node, delta in payload.items():
                    if not isinstance(delta, dict):
                        continue
                    if delta.get("usage"):
                        usage_entries.extend(delta["usage"])
                    if node == "researcher" and delta.get("findings"):
                        for finding in delta["findings"]:
                            yield _sse(
                                "subagent",
                                {
                                    "sub_question": finding.get("sub_question", ""),
                                    "sources": len(delta.get("sources", [])),
                                },
                            )
                            all_sources.extend(delta.get("sources", []))
                    elif node == "chapter_research" and delta.get("chapter_findings"):
                        for finding in delta["chapter_findings"]:
                            all_sources.extend(finding.get("sources", []))
                    elif node == "writer" and delta.get("report"):
                        report = delta["report"]
                        project.report = report
                        if delta.get("chapters_written"):
                            project.chapters = list(delta["chapters_written"])
                        session.add(project)
                        await session.commit()
                    elif node == "supervisor" and delta.get("sub_questions"):
                        record("subagents_planned", {"planned": len(delta["sub_questions"])})
                        yield _sse("subagents_planned", {"sub_questions": delta["sub_questions"]})
                    elif node == "synthesizer":
                        report = delta.get("report")
                        if report and not collected_tokens:
                            for word in report.split(" "):
                                yield _sse("token", {"text": word + " "})
                    await save_trace()

        final_report = (report or "".join(collected_tokens) or "").strip()
        deduped = _dedupe_sources(all_sources)
        if deduped:
            yield _sse("sources", {"sources": deduped})
        project.report = final_report
        project.sources = deduped
        project.usage = sum_usage(usage_entries)
        project.trace = trace
        project.status = ResearchStatus.DONE.value
        session.add(project)
        await session.commit()
        if usage_entries:
            yield _sse("usage", {"usage": project.usage})
        yield _sse("done", {"status": "done"})
    except BaseException:
        logger.info("Lauf abgebrochen (Projekt %s)", project.id)
        trace_snapshot = list(trace)
        project_id_snapshot = project.id

        async def _reset_after_abort() -> None:
            from app.core.db import AsyncSessionLocal

            try:
                async with AsyncSessionLocal() as s:
                    db_project = await s.get(ResearchProject, project_id_snapshot)
                    if db_project is not None:
                        db_project.status = ResearchStatus.QUEUED.value
                        db_project.error = None
                        db_project.trace = trace_snapshot
                        s.add(db_project)
                        await s.commit()
            except Exception:
                logger.warning("Abort-Aufräumen fehlgeschlagen", exc_info=True)

        asyncio.ensure_future(_reset_after_abort())
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Recherche-Lauf gescheitert (Projekt %s)", project.id)
        project.status = ResearchStatus.ERROR.value
        project.error = str(e)
        project.trace = trace
        session.add(project)
        await session.commit()
        yield _sse("error", {"detail": str(e)})


# ============================================================================
# 3) PDF-EXPORT (User-Wunsch: echter Download, kein Browser-Druckdialog)
# ============================================================================
# Pipeline: Markdown → HTML (python-markdown) → PDF (xhtml2pdf/pisa).
# xhtml2pdf ist pure Python (braucht keine System-Libs wie Cairo/Pango)
# und versteht eine brauchbare CSS-Untermenge — für unsere Reports reicht
# das locker: Überschriften, Absätze, Listen, klickbare Links.

# Absichtlich schlichtes, druckfreundliches CSS (xhtml2pdf versteht kein
# Flexbox/Grid — Klassiker wie font/margin/border aber zuverlässig).
# Absichtlich druckfreundliches, sauberes CSS (xhtml2pdf unterstützt
# Tabellen-Layouts, @page und @frame exzellent).
_PDF_CSS = """
@page {
    size: a4 portrait;
    margin: 1.8cm 1.6cm 2.2cm 1.6cm;
    @frame footer {
        -pdf-frame-content: pdfFooter;
        bottom: 0.8cm;
        margin-left: 1.6cm;
        margin-right: 1.6cm;
        height: 0.9cm;
    }
}

body {
    font-family: Helvetica;
    font-size: 10pt;
    line-height: 1.5;
    color: #1e293b;
}

#pdfFooter {
    font-size: 8pt;
    color: #94a3b8;
    border-top: 0.5pt solid #e2e8f0;
    padding-top: 4pt;
}

.footer-table {
    width: 100%;
    border: none;
    margin: 0;
}

.footer-table td {
    border: none;
    padding: 0;
    font-size: 8pt;
    color: #94a3b8;
}

h1 {
    font-size: 17pt;
    color: #0f172a;
    font-weight: bold;
    margin: 0 0 6pt 0;
    padding-bottom: 5pt;
    border-bottom: 2pt solid #2563eb;
    page-break-after: avoid;
}

h2 {
    font-size: 13pt;
    color: #0f172a;
    font-weight: bold;
    margin: 16pt 0 6pt 0;
    padding-bottom: 3pt;
    border-bottom: 0.5pt solid #e2e8f0;
    page-break-after: avoid;
}

h3 {
    font-size: 11pt;
    color: #334155;
    font-weight: bold;
    margin: 12pt 0 4pt 0;
    page-break-after: avoid;
}

h4 {
    font-size: 10pt;
    color: #475569;
    font-weight: bold;
    margin: 8pt 0 3pt 0;
    page-break-after: avoid;
}

p {
    margin: 0 0 7pt 0;
    line-height: 1.5;
}

.meta-box {
    background-color: #f8fafc;
    border: 0.75pt solid #e2e8f0;
    padding: 7pt 10pt;
    margin-bottom: 12pt;
    font-size: 8.5pt;
    color: #64748b;
    border-radius: 3pt;
}

blockquote {
    background-color: #f8fafc;
    border-left: 3pt solid #2563eb;
    margin: 8pt 0 10pt 0;
    padding: 6pt 10pt;
    font-size: 9.5pt;
    color: #334155;
}

blockquote p {
    margin: 0;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 10pt 0 12pt 0;
    page-break-inside: avoid;
}

th {
    background-color: #f1f5f9;
    color: #0f172a;
    font-size: 8.5pt;
    font-weight: bold;
    text-align: left;
    padding: 5pt 7pt;
    border: 0.5pt solid #cbd5e1;
    vertical-align: bottom;
}

td {
    font-size: 8.5pt;
    line-height: 1.35;
    padding: 5pt 7pt;
    border: 0.5pt solid #e2e8f0;
    vertical-align: top;
}

tr:nth-child(even) td {
    background-color: #f8fafc;
}

a {
    color: #2563eb;
    text-decoration: none;
}

a.citation {
    font-weight: bold;
    color: #2563eb;
    font-size: 8.5pt;
}

ul, ol {
    margin: 4pt 0 7pt 14pt;
}

li {
    margin-bottom: 2.5pt;
    font-size: 9.5pt;
    line-height: 1.45;
}

code {
    font-family: Courier;
    font-size: 8.5pt;
    background-color: #f1f5f9;
    color: #0f172a;
    padding: 1pt 3pt;
}

pre {
    font-family: Courier;
    font-size: 8pt;
    background-color: #0f172a;
    color: #f8fafc;
    padding: 7pt 9pt;
    border-radius: 3pt;
    margin: 8pt 0;
    page-break-inside: avoid;
    line-height: 1.35;
}

.sources-list {
    font-size: 8.5pt;
    color: #475569;
    margin-left: 14pt;
}

.sources-list li {
    margin-bottom: 4pt;
    line-height: 1.4;
}
"""


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


_UNICODE_REPLACEMENTS = {
    # Hochgestellte Zeichen (Superscript) -> <sup>
    "⁰": "<sup>0</sup>",
    "¹": "<sup>1</sup>",
    "²": "<sup>2</sup>",
    "³": "<sup>3</sup>",
    "⁴": "<sup>4</sup>",
    "⁵": "<sup>5</sup>",
    "⁶": "<sup>6</sup>",
    "⁷": "<sup>7</sup>",
    "⁸": "<sup>8</sup>",
    "⁹": "<sup>9</sup>",
    "ⁿ": "<sup>n</sup>",
    "ᵗ": "<sup>t</sup>",
    "⁺": "<sup>+</sup>",
    "⁻": "<sup>-</sup>",
    # Tiefgestellte Zeichen (Subscript) -> <sub>
    "₀": "<sub>0</sub>",
    "₁": "<sub>1</sub>",
    "₂": "<sub>2</sub>",
    "₃": "<sub>3</sub>",
    "₄": "<sub>4</sub>",
    "₅": "<sub>5</sub>",
    "₆": "<sub>6</sub>",
    "₇": "<sub>7</sub>",
    "₈": "<sub>8</sub>",
    "₉": "<sub>9</sub>",
    "ᵢ": "<sub>i</sub>",
    "ⱼ": "<sub>j</sub>",
    "ₖ": "<sub>k</sub>",
    "ₗ": "<sub>l</sub>",
    "ₘ": "<sub>m</sub>",
    "ₙ": "<sub>n</sub>",
    # Gedankenstriche
    "—": " &mdash; ",
    "―": " &mdash; ",
    # Typografische Anführungszeichen
    "„": "&bdquo;",
    "“": "&ldquo;",
    "”": "&rdquo;",
    "‘": "&lsquo;",
    "’": "&rsquo;",
    "‚": "&sbquo;",
    "«": "&laquo;",
    "»": "&raquo;",
    # Mathematische Operatoren & Symbole
    "•": "&bull;",
    "·": "&middot;",
    "…": "&hellip;",
    "×": "&times;",
    "÷": "&divide;",
    "±": "&plusmn;",
    "∓": "-/+",
    "≈": "&asymp;",
    "≠": "&ne;",
    "≤": "&le;",
    "≥": "&ge;",
    "∞": "&infin;",
    "√": "&radic;",
    "∑": "&sum;",
    "∏": "&prod;",
    "→": "&rarr;",
    "←": "&larr;",
    "⇒": "&rArr;",
    "⇔": "&hArr;",
    "↑": "&uarr;",
    "↓": "&darr;",
    # Häkchen & Sonderzeichen
    "✓": "&#10003;",
    "✔": "&#10004;",
    "✗": "&#10007;",
    "✖": "&#10008;",
    "™": "&trade;",
    "©": "&copy;",
    "®": "&reg;",
}


def _remove_duplicate_headings(text: str) -> str:
    """
    Entfernt redundante, aufeinanderfolgende Kapitelüberschriften, z. B.
    '## 3. US-Konzerne und Scale-ups' gefolgt von '## US-Konzerne und Scale-ups'
    oder 'US-Konzerne und Scale-ups'.
    """
    if not text:
        return text

    import re

    lines = text.split("\n")
    cleaned_lines: list[str] = []

    def _extract_title(line: str) -> str | None:
        stripped = line.strip()
        if not stripped:
            return None
        is_heading = stripped.startswith("#") or (
            stripped.startswith("**") and stripped.endswith("**")
        )
        if not is_heading and len(stripped) > 150:
            return None
        clean = re.sub(r"^[#\s\*_`]+", "", stripped)
        clean = re.sub(r"^(?:Kapitel\s*)?\d+(?!\.\d)[\.\:\)\-–—\s]+", "", clean)
        clean = re.sub(r"[#\s\*_`]+$", "", clean).strip()
        clean = re.sub(r"[^\w\s]", "", clean).lower().strip()
        return clean or None

    i = 0
    while i < len(lines):
        line = lines[i]
        cleaned_lines.append(line)

        # Prüfen, ob die aktuelle Zeile eine Kapitel-Hauptüberschrift ist (z. B. ## 3. ...)
        m = re.match(r"^##\s+(\d+\.\s*.+)$", line.strip())
        if m:
            main_title = _extract_title(line)
            # Schaue nach vorne über Leerzeilen hinweg zur nächsten nicht-leeren Zeile
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                next_line = lines[j]
                next_title = _extract_title(next_line)
                if (
                    main_title
                    and next_title
                    and (
                        main_title == next_title
                        or main_title.startswith(next_title)
                        or next_title.startswith(main_title)
                    )
                ):
                    # Überspringe next_line und eventuell nachfolgende Leerzeilen
                    j += 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    i = j - 1
        i += 1

    return "\n".join(cleaned_lines)


def _clean_report_text(text: str) -> str:
    """Bereinigt typische LLM-Formatierungsunsauberkeiten und doppelte Überschriften."""
    import re

    # 1) Fehlende Leerzeichen um Währungszeichen (€) beheben (z. B. "45.000 €und", "60.000€aus")
    text = re.sub(r"(\d)€", r"\1 €", text)
    text = re.sub(r"€([A-Za-zäöüÄÖÜ0-9\[])", r"€ \1", text)

    # 2) Doppelte aufeinanderfolgende Kapitelüberschriften entfernen
    text = _remove_duplicate_headings(text)

    # 3) Unschöne Denglisch-Wortschöpfungen bereinigen
    text = re.sub(r"\bCompensation-Modell(en?|s)?\b", r"Vergütungsmodell\1", text)
    text = re.sub(r"\bCompensation-Benchmark(s)?\b", r"Gehaltsbenchmark\1", text)

    return text


def _extract_sources_from_text(text: str) -> dict[int, dict]:
    """Liest Quellen aus dem Literaturverzeichnis am Textende aus (Fallback/Ergänzung)."""
    import re

    sources_by_num: dict[int, dict] = {}
    # [n] ... http...
    for m in re.finditer(r"^\s*\[(\d+)\]\s*(.*?)(https?://[^\s\)]+)", text, re.MULTILINE):
        num = int(m.group(1))
        title = (
            m.group(2)
            .strip()
            .strip('“"”')
            .rstrip("—")
            .rstrip("[Online].")
            .rstrip("Verfügbar:")
            .strip()
        )
        url = m.group(3).strip().rstrip('.,;)"')
        sources_by_num[num] = {"title": title or url, "url": url}

    # n. ... http...
    for m in re.finditer(r"^\s*(\d+)\.\s*(.*?)(https?://[^\s\)]+)", text, re.MULTILINE):
        num = int(m.group(1))
        if num not in sources_by_num:
            title = m.group(2).strip()
            url = m.group(3).strip().rstrip('.,;)"')
            sources_by_num[num] = {"title": title or url, "url": url}

    return sources_by_num


def linkify_citations(report: str, sources: list[dict] | None) -> str:
    """Wandelt Zitationen ([n], [n, m], [n-m], [n–m], [Quelle n], [vgl. n]) in klickbare Links um."""
    if not report:
        return report

    import re

    # 1) Quellenverzeichnis aufbauen (Prio: übergebene sources -> aus Text extrahierte Quellen)
    source_map: dict[int, dict] = {}
    if sources:
        for i, s in enumerate(sources, start=1):
            if s and s.get("url"):
                source_map[i] = s

    text_sources = _extract_sources_from_text(report)
    for num, s in text_sources.items():
        if num not in source_map or not source_map[num].get("url"):
            source_map[num] = s

    if not source_map:
        return report

    # 2) Text in Hauptteil und Literaturverzeichnis trennen
    bib_match = re.search(
        r"(?:\n|\A)\s*(##\s+(?:Literaturverzeichnis|Quellen)[\s\S]*)$",
        report,
        flags=re.IGNORECASE,
    )
    if bib_match:
        main_text = report[: bib_match.start(1)]
        bib_text = report[bib_match.start(1) :]
    else:
        main_text = report
        bib_text = ""

    parts = re.split(r"(```[\s\S]*?```|`[^`\n]*`)", main_text)

    def _format_single_link(n: int) -> str:
        s = source_map.get(n)
        if s and s.get("url"):
            raw_title = s.get("title") or s.get("url") or f"Quelle {n}"
            safe_title = re.sub(r"[\r\n\t|]+", " ", raw_title).strip().replace('"', "'")
            safe_url = (
                s["url"]
                .strip()
                .replace("\r", "")
                .replace("\n", "")
                .replace(" ", "%20")
                .replace("|", "%7C")
            )
            return f'[{n}]({safe_url} "{safe_title}")'
        return f"[{n}]"

    def _replace_brackets(m: re.Match) -> str:
        inner = m.group(1).strip()
        if m.group(0).endswith("](") or "(http" in inner:
            return m.group(0)

        # Präfix wie "Quelle", "Quellen", "Ref.", "vgl.", "siehe" bereinigen
        clean_inner = re.sub(
            r"^(?:Quellen?|Ref\.?|vgl\.?|siehe)\s*:?\s*", "", inner, flags=re.IGNORECASE
        ).strip()
        tokens = [t.strip() for t in re.split(r"[,;]+|\s+", clean_inner) if t.strip()]
        if not tokens:
            return m.group(0)

        links = []
        has_valid_citation = False

        for tok in tokens:
            # Bereich wie "1-4" oder "1–4" oder "1 — 4"
            range_match = re.match(r"^(\d+)\s*[-–—]\s*(\d+)$", tok)
            if range_match:
                start, end = int(range_match.group(1)), int(range_match.group(2))
                if start <= end and end - start <= 25:
                    for n in range(start, end + 1):
                        links.append(_format_single_link(n))
                        has_valid_citation = True
                    continue

            # Einzelne Zahl wie "14"
            if tok.isdigit():
                n = int(tok)
                links.append(_format_single_link(n))
                has_valid_citation = True
            else:
                links.append(tok)

        if has_valid_citation:
            return " ".join(links)
        return m.group(0)

    for i in range(0, len(parts), 2):
        parts[i] = re.sub(
            r"\[((?:(?:Quellen?|Ref\.?|vgl\.?|siehe)\s*:?\s*)?[0-9\s,\-–—]+)\](?!\()",
            _replace_brackets,
            parts[i],
            flags=re.IGNORECASE,
        )

    return "".join(parts) + bib_text


def _report_markdown_for_pdf(project: ResearchProject) -> str:
    """
    Markdown für den PDF-Export.

    Ist der finale Report vorhanden, wird er verwendet (die Assembly bringt
    bereits Titel, Abstract und Inhaltsverzeichnis mit). Sonst wird aus der
    persistierten Gliederung + den bereits geschriebenen Kapiteln ein
    TEILBERICHT gebaut — mit Inhaltsverzeichnis, in dem geplante, aber noch
    nicht geschriebene Kapitel markiert sind (Lauf abgebrochen).
    """
    if project.report:
        return project.report

    chapters = project.chapters or []
    if not chapters:
        raise ValueError("Noch kein Report vorhanden — erst die Recherche laufen lassen.")

    outline = project.outline or {}
    parts: list[str] = [f"# {outline.get('title') or project.title or project.question}\n"]
    if outline.get("abstract"):
        parts.append(f"> **Abstract:** {outline['abstract']}\n")
    parts.append(
        "> **Teilbericht** — der Lauf wurde abgebrochen; die unten markierten "
        "Kapitel fehlen noch.\n"
    )

    written = {c.get("title") for c in chapters}
    planned = [c.get("title", "") for c in outline.get("chapters", [])]
    if planned:
        parts.append("## Inhaltsverzeichnis\n")
        for i, title in enumerate(planned, start=1):
            if title in written:
                parts.append(f"{i}. {title}")
            else:
                parts.append(f"{i}. {title} *(geplant — noch nicht geschrieben)*")
        parts.append("")

    from app.agent.deep_report import clean_chapter_content

    for i, ch in enumerate(chapters, start=1):
        clean_content = clean_chapter_content(ch.get("content", ""), ch.get("title", ""))
        parts.append(f"## {i}. {ch.get('title', '')}\n\n{clean_content}\n")
    return "\n".join(parts)


def report_to_pdf(project: ResearchProject) -> bytes:
    """
    Baut aus Frage + Report + Quellen ein PDF und liefert die rohen Bytes.

    Wirft ValueError, wenn weder ein Report noch geschriebene Kapitel
    existieren (Endpunkt → 409).
    """
    report_md = _report_markdown_for_pdf(project)

    import io
    import re
    from datetime import UTC

    import markdown
    from xhtml2pdf import pisa

    # 1) Report-Markdown bereinigen & Zitate verlinken
    cleaned_md = _clean_report_text(report_md)
    linkified_report = linkify_citations(cleaned_md, project.sources)
    report_html = markdown.markdown(linkified_report, extensions=["extra", "sane_lists"])

    # Zitat-Links im HTML mit eckigen Klammern [n] versehen & mit citation-Klasse stylen
    report_html = re.sub(
        r'<a\s+href="([^"]+)"(?:\s+title="([^"]*)")?>\s*(\d+)\s*</a>',
        r'<a href="\1" class="citation" title="\2">[\3]</a>',
        report_html,
    )

    # Unicode-Zeichen, die im Standard-PDF-Font als schwarzes Quadrat enden würden,
    # im fertigen HTML sicher durch Entities ersetzen (stört Markdown nicht)
    for char, replacement in _UNICODE_REPLACEMENTS.items():
        if char in report_html:
            report_html = report_html.replace(char, replacement)

    # 2) Quellenliste anhängen (falls im Report noch kein eigenes Verzeichnis vorhanden)
    has_bib = bool(
        re.search(r"<h[1-6]>[^<]*(?:Literaturverzeichnis|Quellen)", report_html, re.IGNORECASE)
    )
    if not has_bib and project.sources:
        source_items = "".join(
            '<li><a href="{url}">{title}</a></li>'.format(
                url=_escape_html(s.get("url", "")),
                title=_escape_html(s.get("title") or s.get("url", "")),
            )
            for s in project.sources
        )
        sources_html = f'<h2>Quellen</h2><ol class="sources-list">{source_items}</ol>'
    else:
        sources_html = ""

    # 3) Kopfzeile mit Metadaten (Datum, Tiefe, Zitierstil, Status)
    created = project.created_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M")
    depth_val = getattr(project, "depth", None)
    depth_label = "Deep Research" if depth_val == "deep" else "Standard (Quick)"
    style_val = getattr(project, "citation_style", None) or "ieee"
    style_label = style_val.upper()
    status_label = (
        "Abgeschlossen"
        if project.status == ResearchStatus.DONE.value
        else _escape_html(project.status)
    )

    meta_header = f"""
    <div class="meta-box">
      <strong>Fuchser Deep Research Report</strong> · {created} (UTC)<br/>
      Tiefe: {depth_label} · Zitierstil: {style_label} · Status: {status_label}
    </div>
    """

    footer_html = """
    <div id="pdfFooter">
      <table class="footer-table">
        <tr>
          <td style="text-align: left;">Fuchser Deep Research · Vertraulicher Analysebericht</td>
          <td style="text-align: right;">Seite <pdf:pageNumber/> von <pdf:pageCount/></td>
        </tr>
      </table>
    </div>
    """

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/><style>{_PDF_CSS}</style></head>
    <body>
      {footer_html}
      {meta_header}
      {report_html}
      {sources_html}
    </body></html>"""

    # 4) HTML → PDF. pisa schreibt in ein BytesIO; Fehler landen in
    #    err (Log-Blick lohnt sich, wenn mal etwas komisch aussieht).
    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if result.err:
        logger.error("PDF-Generierung mit Fehlern: %s", result.err)
    pdf_bytes = buffer.getvalue()
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("PDF-Generierung fehlgeschlagen.")
    return pdf_bytes


# ============================================================================
# 4) STAGE 3: Deep-Report nach Outline-Freigabe fortsetzen
# ============================================================================
async def resume_research_stream(
    session: AsyncSession,
    project: ResearchProject,
    outline: dict,
) -> AsyncIterator[str]:
    """
    Resumed einen pausierten Deep-Report mit der freigegebenen Gliederung.

    Der Graph wurde am interrupt() nach der Outline pausiert; hier wird er
    mit Command(resume=outline) weitergefuehrt — der Rest der Pipeline
    (Kapitel-Recherche, Writer, Assembly) laeuft wie bei /run.
    """
    from langgraph.types import Command

    from app.agent.deep_report import build_deep_report_graph

    yield _sse("status", {"status": "resumed"})

    graph = build_deep_report_graph(checkpointer=get_checkpointer())
    config = {"configurable": {"thread_id": str(project.thread_id)}}

    collected_tokens: list[str] = []
    all_sources: list[dict] = []
    usage_entries: list[dict] = []
    trace: list[dict] = []
    report: str | None = None

    def record(event: str, data: dict) -> None:
        if len(trace) > 500:
            return
        entry = {"t": datetime.now(UTC).isoformat(), "event": event}
        for key in (
            "node",
            "status",
            "verdict",
            "gaps",
            "revision",
            "sub_question",
            "planned",
            "findings",
            "echo",
            "index",
            "title",
            "words",
        ):
            if key in data:
                entry[key] = data[key]
        trace.append(entry)

    try:
        async for mode, payload in graph.astream(
            Command(resume=outline),
            config,
            stream_mode=["custom", "messages", "updates"],
        ):
            if mode == "custom":
                yield _sse(payload.get("event", "node"), payload)
                record(payload.get("event", "node"), payload)
                if payload.get("node") == "synthesizer" and payload.get("status") == "start":
                    collected_tokens.clear()
                    yield _sse("report_reset", {})
            elif mode == "messages":
                chunk, metadata = payload if isinstance(payload, tuple) else (payload, {})
                node = metadata.get("langgraph_node", "") if isinstance(metadata, dict) else ""
                text = getattr(chunk, "content", "")
                if node == "writer" and isinstance(text, str) and text:
                    # Deep-Reports streamen über chapter events, nicht tokens
                    pass
            elif mode == "updates":
                for node, delta in payload.items():
                    if not isinstance(delta, dict):
                        continue
                    if delta.get("usage"):
                        usage_entries.extend(delta["usage"])
                    if node == "chapter_research" and delta.get("chapter_findings"):
                        for finding in delta["chapter_findings"]:
                            all_sources.extend(finding.get("sources", []))
                    elif node == "writer" and delta.get("report"):
                        report = delta["report"]

        final_report = (report or "").strip()
        deduped = _dedupe_sources(all_sources)
        project.report = final_report
        project.sources = deduped
        project.usage = sum_usage(usage_entries)
        project.trace = trace
        project.status = ResearchStatus.DONE.value
        session.add(project)
        await session.commit()
        if usage_entries:
            yield _sse("usage", {"usage": project.usage})
        yield _sse("done", {"status": "done"})
    except BaseException:
        logger.info("Resume abgebrochen (Projekt %s)", project.id)
        trace_snapshot = list(trace)
        project_id_snapshot = project.id

        async def _reset_after_abort() -> None:
            from app.core.db import AsyncSessionLocal

            try:
                async with AsyncSessionLocal() as s:
                    db_project = await s.get(ResearchProject, project_id_snapshot)
                    if db_project is not None:
                        db_project.status = ResearchStatus.QUEUED.value
                        db_project.error = None
                        db_project.trace = trace_snapshot
                        s.add(db_project)
                        await s.commit()
            except Exception:
                pass

        asyncio.ensure_future(_reset_after_abort())
        raise
    except Exception as e:
        logger.exception("Resume fehlgeschlagen (Projekt %s)", project.id)
        project.status = ResearchStatus.ERROR.value
        project.error = str(e)
        project.trace = trace
        session.add(project)
        await session.commit()
        yield _sse("error", {"detail": str(e)})


# ============================================================================
# 5) ADMIN-SICHT (Stufe 5): alle Recherchen aller User
# ============================================================================
async def list_all_projects(session: AsyncSession) -> list[dict]:
    """
    Alle Projekte (aller User) mit Besitzer-E-Mail — nur für Admins
    (der Endpunkt hängt RequireAdmin davor).

    Join per Hand (statt SQLModel-Relationship): Im async-Kontext sind
    explizite SELECTs sauberer als lazy-loaded Relationships.
    """
    result = await session.exec(
        select(ResearchProject, User.email)
        .join(User, ResearchProject.user_id == User.id)  # type: ignore[arg-type]
        .order_by(ResearchProject.created_at.desc())
        .limit(200)
    )
    return [
        {
            "id": project.id,
            "question": project.question,
            "status": project.status,
            "user_email": email,
            "usage": project.usage,
            "parent_id": project.parent_id,
            "created_at": project.created_at,
        }
        for project, email in result.all()
    ]
