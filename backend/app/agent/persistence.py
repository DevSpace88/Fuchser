"""
agent/persistence.py — the LangGraph checkpointer on our Postgres
===================================================================

THE reason LangGraph is exciting here: a *checkpointer* stores a graph
run's state per *thread* (thread_id) in the database.

    * Browser refresh? State gone? NO — it lives in Postgres.
    * Backend restart mid-research? Resumable.
    * Follow-up question? The new run sees the old state (memory).

AsyncPostgresSaver creates its own tables
(checkpoints, checkpoint_writes, checkpoint_blobs) — they do NOT belong
in Alembic; `await saver.setup()` handles that idempotently at startup.

Pool budget (FastAPI skill rule): the saver holds its OWN connections.
workers × (app pool + saver connections) must stay below Postgres
max_connections — that's why we deliberately configure it small and
close it cleanly on shutdown.
"""

import logging
from contextlib import AsyncExitStack

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings

logger = logging.getLogger(__name__)

# Module-global: exactly ONE saver per process (lifespan rule: pool everything).
_exit_stack: AsyncExitStack | None = None
_saver: BaseCheckpointSaver | None = None


async def init_checkpointer() -> BaseCheckpointSaver:
    """
    Starts the Postgres checkpointer in the lifespan.

    If Postgres is down (e.g. local tests without a DB), we degrade to
    InMemorySaver: the app runs, persistence is then process-local only.
    """
    global _exit_stack, _saver
    try:
        _exit_stack = AsyncExitStack()
        cm = AsyncPostgresSaver.from_conn_string(settings.agent_database_url)
        saver = await _exit_stack.enter_async_context(cm)
        await saver.setup()  # creates the checkpoint tables (idempotent)
        _saver = saver
        logger.info(
            "LangGraph-Checkpointer: Postgres (%s)",
            settings.agent_database_url.split("@")[-1],
        )
    except Exception as e:  # noqa: BLE001 — deliberately broad: app must keep running
        logger.warning("Postgres-Checkpointer nicht verfügbar (%s) — nutze InMemorySaver", e)
        _saver = InMemorySaver()
    return _saver


async def shutdown_checkpointer() -> None:
    """Closes the saver + its connections when the app stops."""
    global _exit_stack, _saver
    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack = None
    _saver = None


def get_checkpointer() -> BaseCheckpointSaver:
    """
    Returns the active saver (there is always one — fallback InMemorySaver).

    Callers are typically services that compile a graph.
    """
    if _saver is None:
        return InMemorySaver()
    return _saver
