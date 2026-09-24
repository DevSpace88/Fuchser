"""
scripts/smoke_stage0.py — Proof of function for Stage 0 (see PLAN.md)
=====================================================================

Proves THREE things:
    1. The mini graph runs (state → node → answer).
    2. The AsyncPostgresSaver stores checkpoints in OUR Postgres DB
       (row in the `checkpoints` table).
    3. Thread persistence: a second run on the same thread_id finds
       the old state again (history grows).

Invocation (locally, without the Docker backend — the DB runs via docker compose):
    POSTGRES_HOST=localhost uv run python scripts/smoke_stage0.py

Without DEEPSEEK_API_KEY the node runs in echo mode — the checkpoint
proof still works.
"""

import asyncio
import sys
import uuid
from pathlib import Path

# The script lives in backend/scripts/ — put backend/ on sys.path so that
# `app.*` is importable (as when started via `uvicorn app.main:app`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg

from app.agent.graph import build_mini_graph
from app.agent.persistence import init_checkpointer, shutdown_checkpointer
from app.core.config import settings


async def count_checkpoints(conn: asyncpg.Connection, thread_id: str) -> int:
    # checkpoint_blobs contains entries per (thread_id, checkpoint) —
    # DISTINCT over checkpoint_hs suffices as proof of existence.
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

    # --- Run 1 ---
    result = await graph.ainvoke({"question": "Was ist LangGraph?"}, config)
    print(f"\nAntwort (Lauf 1): {result['answer'][:120]}")

    # --- Persistence proof directly in Postgres ---
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
