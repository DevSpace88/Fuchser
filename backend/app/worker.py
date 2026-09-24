"""
worker.py — Eigenständiger Worker-Prozess für Graph-Ausführung
================================================================

Läuft als SEPARATER Prozess/Container und nimmt Tasks aus der Redis-Queue
entgegen. Führt den LangGraph-Graphen aus und publish't Fortschritt via
Redis PubSub. Unabhängig vom API-Server — Browser-Reloads können den
Agenten NICHT mehr töten.

Start:  python -m app.worker
Docker: gleiche Image wie Backend, anderes Command
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(message)s")
logger = logging.getLogger(__name__)


async def process_project(project_id: str) -> None:
    """Führt den Graphen für EIN Projekt aus (mit eigener DB-Session)."""
    from app.core.db import AsyncSessionLocal
    from app.core.redis import clear_run_marker, publish_progress, refresh_run_marker
    from app.models.research_project import ResearchProject

    async with AsyncSessionLocal() as session:
        project = await session.get(ResearchProject, project_id)
        if project is None:
            logger.error("Projekt %s nicht gefunden", project_id)
            return

        logger.info("Starte Graph für: %s", project.question[:60])
        project.status = "running"
        session.add(project)
        await session.commit()
        await publish_progress(project_id, "status", {"status": "running"})

        # Heartbeat: Run-Marker alle 15s erneuern, damit die API erkennt,
        # dass dieser Task noch lebt (stirbt der Worker, verfällt der Marker
        # und ein Reconnect reiht den Lauf mit Resume neu ein).
        async def _heartbeat() -> None:
            while True:
                await refresh_run_marker(project_id)
                await asyncio.sleep(15)

        heartbeat = asyncio.create_task(_heartbeat())
        try:
            await _run_graph(session, project)
        except Exception as e:
            logger.exception("Graph fehlgeschlagen: %s", e)
            project.status = "error"
            project.error = str(e)[:500]
            session.add(project)
            await session.commit()
            await publish_progress(project_id, "error", {"detail": str(e)[:200]})
        finally:
            heartbeat.cancel()
            await clear_run_marker(project_id)


async def _run_graph(session, project) -> None:
    """Der eigentliche Graph-Lauf mit Redis-PubSub für Fortschritt."""
    from datetime import UTC, datetime

    from app.agent.deep_report import build_deep_report_graph
    from app.agent.graph import build_research_graph
    from app.agent.persistence import get_checkpointer
    from app.agent.usage import sum_usage
    from app.core.redis import publish_progress
    from app.services.document_service import (
        documents_for_agent,
        list_documents_for_chain,
    )
    from app.services.research_service import _dedupe_sources

    if project.depth == "deep":
        graph = build_deep_report_graph(checkpointer=get_checkpointer())
    else:
        graph = build_research_graph(checkpointer=get_checkpointer())

    config = {"configurable": {"thread_id": str(project.thread_id)}}
    docs = documents_for_agent(await list_documents_for_chain(session, project))

    # RESUME-Semantik: Teilkapitel vorhanden, aber kein fertiger Report ->
    # abgebrochenen Lauf fortsetzen. Der Graph startet mit Input, aber die
    # Nodes sind IDEMPOTENT: Outline-Planung + Kapitel-Recherche überspringen
    # alles, was im Checkpoint-State schon vorhanden ist, und der Writer
    # überspringt die gespeicherten Kapitel. => Nur die FEHLENDEN Kapitel
    # kosten Tokens — Outline/Recherche werden nicht erneut bezahlt.
    resuming = bool(project.chapters) and not project.report
    if not resuming:
        project.chapters = None
        session.add(project)
        await session.commit()
    graph_input = {
        "question": project.question,
        "context_summary": project.context_summary or "",
        "documents": docs,
        "citation_style": project.citation_style or "ieee",
        "chapters_written": list(project.chapters or []) if resuming else [],
    }

    trace: list[dict] = []
    all_sources: list[dict] = []
    usage_entries: list[dict] = []
    report: str | None = None
    collected_tokens: list[str] = []
    last_trace_save = 0

    def record(event: str, data: dict) -> None:
        if len(trace) > 500:
            return
        entry = {"t": datetime.now(UTC).isoformat(), "event": event}
        for key in (
            "node", "status", "verdict", "gaps", "revision",
            "sub_question", "planned", "index", "title", "words",
        ):
            if key in data:
                entry[key] = data[key]
        trace.append(entry)

    async def save_and_publish(event: str, data: dict) -> None:
        """Event in Trace aufnehmen, in DB persistieren, an Redis publishen."""
        nonlocal last_trace_save
        record(event, data)
        await publish_progress(project.id, event, data)
        # Inkrementell persistieren (alle 5 Events)
        if len(trace) - last_trace_save >= 5:
            last_trace_save = len(trace)
            project.trace = list(trace)
            session.add(project)
            await session.commit()

    async for mode, payload in graph.astream(
        graph_input,
        config,
        stream_mode=["custom", "messages", "updates"],
    ):
        if mode == "custom":
            await save_and_publish(payload.get("event", "node"), payload)
            if payload.get("event") == "outline" and payload.get("outline"):
                project.outline = payload["outline"]
                session.add(project)
                await session.commit()
            elif (
                payload.get("event") == "chapter"
                and payload.get("status") == "done"
                and payload.get("content")
            ):
                # Kapitel SOFORT persistieren: bricht der Lauf beim nächsten
                # Kapitel ab, sind alle fertigen Kapitel schon in der DB.
                chapters = list(project.chapters or [])
                chapters.append(
                    {"title": payload.get("title", ""), "content": payload["content"]}
                )
                project.chapters = chapters
                session.add(project)
                await session.commit()

        elif mode == "messages":
            chunk, metadata = payload if isinstance(payload, tuple) else (payload, {})
            node = metadata.get("langgraph_node", "") if isinstance(metadata, dict) else ""
            text = getattr(chunk, "content", "")
            if node == "synthesizer" and isinstance(text, str) and text:
                collected_tokens.append(text)
                await publish_progress(project.id, "token", {"text": text})

        elif mode == "updates":
            for node, delta in payload.items():
                if not isinstance(delta, dict):
                    continue
                if delta.get("usage"):
                    usage_entries.extend(delta["usage"])
                if node == "researcher" and delta.get("findings"):
                    for finding in delta["findings"]:
                        await publish_progress(
                            project.id,
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
                elif node in ("synthesizer", "writer") and delta.get("report"):
                    report = delta["report"]
                    project.report = report
                    if delta.get("chapters_written"):
                        project.chapters = list(delta["chapters_written"])
                    session.add(project)
                    await session.commit()
                elif node == "supervisor" and delta.get("sub_questions"):
                    await publish_progress(
                        project.id,
                        "subagents_planned",
                        {"sub_questions": delta["sub_questions"]},
                    )
                await save_and_publish("update", {"node": node})

    final_report = (report or "".join(collected_tokens) or "").strip()
    if not final_report and project.depth == "deep":
        final_report = project.report or ""

    deduped = _dedupe_sources(all_sources)
    project.report = final_report
    project.sources = deduped
    project.usage = sum_usage(usage_entries)
    project.trace = trace
    project.status = "done"
    session.add(project)
    await session.commit()

    await publish_progress(project.id, "usage", {"usage": project.usage})
    await publish_progress(project.id, "sources", {"sources": deduped})
    await publish_progress(project.id, "done", {"status": "done"})
    logger.info("Fertig: %s (%d Wörter)", project.question[:40], len(final_report.split()))


async def _requeue_orphaned_runs() -> int:
    """Start-Scan (Selbstheilung nach Crash/Redeploy): Läufe mit Status
    'running', die weder in der Queue liegen noch einen legitimen Besitzer
    haben, neu einordnen — Dank Kapitel-Persistenz + idempotenter Nodes
    GÜNSTIG ab dem letzten fertigen Kapitel statt von vorne.

    Annahme: genau EIN Worker-Container (siehe docker-compose.yml). Da DIESER
    Worker gerade erst startet, kann kein anderer Worker leben — die vier
    Fälle sind damit eindeutig:
      * Task in Queue            -> nichts tun (BLPOP holt ihn)
      * nicht in Queue, Marker   -> Leftover-Marker (Redeploy < TTL) -> verwaist
      * nicht in Queue, kein M.  -> Worker starb, Marker verfallen     -> verwaist
    """
    from sqlmodel import select

    from app.core.db import AsyncSessionLocal
    from app.core.redis import has_run_marker, push_task, queue_contains, set_run_marker
    from app.models.research_project import ResearchProject

    requeued = 0
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ResearchProject).where(ResearchProject.status == "running")
        )
        orphans = list(result.all())
    for project in orphans:
        pid = str(project.id)
        if await queue_contains(pid):
            continue  # liegt noch in der Queue — BLPOP kümmert sich
        if await has_run_marker(pid):
            logger.warning(
                "Lauf %s: Run-Marker lebt, aber kein Task in der Queue — "
                "Leftover nach Redeploy, wird neu eingeordnet",
                pid,
            )
        await set_run_marker(pid)
        await push_task(pid)
        requeued += 1
        logger.warning("Verwaisten Lauf neu eingeordnet (Resume): %s", pid)
    return requeued


async def main() -> None:
    """Worker-Main-Loop: Tasks aus Redis-Queue nehmen und abarbeiten."""
    from app.core.redis import pop_task

    logger.info("🦊 Fuchser-Worker gestartet — warte auf Tasks …")

    # Checkpointer initialisieren (für Graph-Persistenz)
    from app.agent.persistence import init_checkpointer
    await init_checkpointer()

    # Selbstheilung: Läufe aus einem Absturz des Vorgänger-Workers neu
    # einordnen (Resume ab letztem Kapitel), bevor neue Tasks angenommen werden.
    try:
        orphans = await _requeue_orphaned_runs()
        if orphans:
            logger.info("Start-Scan: %d verwaiste Läufe neu eingeordnet", orphans)
    except Exception:  # noqa: BLE001 — Scan darf den Start nie blockieren
        logger.exception("Start-Scan fehlgeschlagen — Worker läuft trotzdem")

    while True:
        try:
            project_id = await pop_task(timeout=5)
            if project_id is None:
                continue  # Timeout — weiter pollen

            logger.info("Task erhalten: %s", project_id)
            await process_project(project_id)

        except KeyboardInterrupt:
            logger.info("Worker wird heruntergefahren.")
            break
        except Exception:
            logger.exception("Worker-Fehler — weitermachen")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
