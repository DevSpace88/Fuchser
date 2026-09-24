"""
agent/persistence.py — LangGraph-Checkpointer auf unserem Postgres
===================================================================

DER Grund, warum LangGraph hier spannend ist: Ein *Checkpointer* speichert
den State eines Graph-Laufs pro *Thread* (thread_id) in der Datenbank.

    * Browser-Refresh? State ist weg? NEIN — steht in Postgres.
    * Backend-Restart mitten in der Recherche? Weitermachbar.
    * Follow-up-Frage? Der neue Lauf sieht den alten State (Memory).

Der AsyncPostgresSaver legt seine eigenen Tabellen an
(checkpoints, checkpoint_writes, checkpoint_blobs) — die gehören NICHT in
Alembic, `await saver.setup()` erledigt das idempotent beim Start.

Pool-Budget (FastAPI-Skill-Regel): Der Saver hält EIGENE Verbindungen.
workers × (App-Pool + Saver-Verbindungen) muss unter Postgres
max_connections bleiben — deshalb konfigurieren wir ihn bewusst klein
und schließen ihn sauber beim Shutdown.
"""

import logging
from contextlib import AsyncExitStack

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings

logger = logging.getLogger(__name__)

# Modul-global: genau EIN Saver pro Prozess (Lifespan-Regel: pool everything).
_exit_stack: AsyncExitStack | None = None
_saver: BaseCheckpointSaver | None = None


async def init_checkpointer() -> BaseCheckpointSaver:
    """
    Startet den Postgres-Checkpointer im Lifespan.

    Fällt Postgres aus (z. B. lokale Tests ohne DB), degradieren wir auf
    InMemorySaver: die App läuft, Persistenz ist dann nur prozess-lokal.
    """
    global _exit_stack, _saver
    try:
        _exit_stack = AsyncExitStack()
        cm = AsyncPostgresSaver.from_conn_string(settings.agent_database_url)
        saver = await _exit_stack.enter_async_context(cm)
        await saver.setup()  # legt die Checkpoint-Tabellen an (idempotent)
        _saver = saver
        logger.info(
            "LangGraph-Checkpointer: Postgres (%s)",
            settings.agent_database_url.split("@")[-1],
        )
    except Exception as e:  # noqa: BLE001 — bewusst breit: App soll weiterlaufen
        logger.warning("Postgres-Checkpointer nicht verfügbar (%s) — nutze InMemorySaver", e)
        _saver = InMemorySaver()
    return _saver


async def shutdown_checkpointer() -> None:
    """Schließt den Saver + seine Verbindungen beim App-Stopp."""
    global _exit_stack, _saver
    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack = None
    _saver = None


def get_checkpointer() -> BaseCheckpointSaver:
    """
    Liefert den aktiven Saver (immer einer — Fallback InMemorySaver).

    Aufrufer sind typischerweise Services, die einen Graph kompilieren.
    """
    if _saver is None:
        return InMemorySaver()
    return _saver
