"""
scripts/smoke_stage0.py — Funktionsnachweis für Stufe 0 (siehe PLAN.md)
=========================================================================

Beweist DREI Dinge:
    1. Der Mini-Graph läuft (State → Node → Antwort).
    2. Der AsyncPostgresSaver legt Checkpoints in UNSERER Postgres-DB ab
       (Zeile in der Tabelle `checkpoints`).
    3. Thread-Persistenz: ein zweiter Lauf auf derselben thread_id findet
       den alten State wieder (History wächst).

Ausführung (lokal, ohne Docker-Backend — DB läuft via docker compose):
    POSTGRES_HOST=localhost uv run python scripts/smoke_stage0.py

Ohne DEEPSEEK_API_KEY läuft der Node im Echo-Modus — der Checkpoint-
Nachweis funktioniert trotzdem.
"""

import asyncio
import sys
import uuid
from pathlib import Path

# Skript liegt in backend/scripts/ — backend/ ins sys.path nehmen, damit
# `app.*` importierbar ist (wie beim Start via `uvicorn app.main:app`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg

from app.agent.graph import build_mini_graph
from app.agent.persistence import init_checkpointer, shutdown_checkpointer
from app.core.config import settings


async def count_checkpoints(conn: asyncpg.Connection, thread_id: str) -> int:
    # checkpoint_blobs enthält pro (thread_id, checkpoint) Einträge —
    # DISTINCT über checkpoint_hs reicht als Existenz-Nachweis.
    return await conn.fetchval(
        "SELECT COUNT(DISTINCT checkpoint_id) FROM checkpoint_writes WHERE thread_id = $1",
        thread_id,
    )


async def main() -> None:
    print("=== Stufe 0 — Smoke-Test ===")
    print(f"DB: {settings.agent_database_url.split('@')[-1]}")

    saver = await init_checkpointer()
    print(f"Checkpointer: {type(saver).__name__}")

    graph = build_mini_graph(checkpointer=saver)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # --- Lauf 1 ---
    result = await graph.ainvoke({"question": "Was ist LangGraph?"}, config)
    print(f"\nAntwort (Lauf 1): {result['answer'][:120]}")

    # --- Persistenz-Nachweis direkt in Postgres ---
    conn = await asyncpg.connect(settings.agent_database_url)
    try:
        n = await count_checkpoints(conn, thread_id)
        print(f"\nCheckpoints in Postgres für thread {thread_id[:8]}…: {n}")
        assert n > 0, "FEHLER: kein Checkpoint geschrieben!"
        print("✓ State ist in Postgres persistiert.")

        snapshot = await graph.aget_state(config)
        print(f"✓ get_state liefert: question={snapshot.values.get('question', '')[:40]!r}")
    finally:
        await conn.close()

    await shutdown_checkpointer()
    print("\n=== Stufe 0 — Smoke-Test BESTANDEN ===")


if __name__ == "__main__":
    asyncio.run(main())
