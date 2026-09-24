"""
core/redis.py — Redis-Verbindung + Queue/PubSub Helfer
=======================================================

Architektur (reload-sichere Graph-Ausführung):
    1. API pushed project_id auf eine Redis-Queue (LPUSH)
    2. Worker (eigener Prozess) nimmt Tasks entgegen (BLPOP)
    3. Worker führt den Graphen aus und PUBLISHed Fortschritt
    4. SSE-Endpoint SUBSCRIBEd auf den Fortschritts-Channel
    5. Wenn SSE disconnectet → Worker läuft WEITER (Redis-Puffer)

Coolify-kompatibel: REDIS_URL env-var (z.B. redis://redis:6379/0).
"""

import logging
from collections.abc import AsyncIterator

from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.client import PubSub

from app.core.config import settings

logger = logging.getLogger(__name__)

# Queue für anstehende Graph-Läufe
QUEUE_KEY = "fuchser:queue"

# PubSub-Channel pro Projekt: fuchser:progress:{project_id}
def progress_channel(project_id: str) -> str:
    return f"fuchser:progress:{project_id}"


# ----------------------------------------------------------------------------
# Lauf-Marker (Heartbeat): zeigt, dass für ein Projekt gerade ein Task
# aktiv ist (in der Queue oder im Worker). Die API setzt ihn beim Einreihen
# (TTL 120s), der Worker erneuert ihn alle 15s (TTL 60s) — stirbt der
# Worker, verfällt der Marker und die API kann den Lauf neu einreihen.
# ----------------------------------------------------------------------------
def run_marker_key(project_id: str) -> str:
    return f"fuchser:run:{project_id}"


async def set_run_marker(project_id: str, ttl: int = 120) -> None:
    """API: Marker beim Einreihen setzen (lang genug für die Queue-Wartezeit)."""
    r = await get_redis()
    await r.set(run_marker_key(project_id), "1", ex=ttl)


async def refresh_run_marker(project_id: str, ttl: int = 60) -> None:
    """Worker: Marker während der Ausführung am Leben halten (Heartbeat)."""
    r = await get_redis()
    await r.set(run_marker_key(project_id), "1", ex=ttl)


async def has_run_marker(project_id: str) -> bool:
    r = await get_redis()
    return bool(await r.exists(run_marker_key(project_id)))


async def clear_run_marker(project_id: str) -> None:
    """Worker: Marker nach Abschluss löschen (done ODER error)."""
    r = await get_redis()
    await r.delete(run_marker_key(project_id))


_pool: AsyncRedis | None = None


async def get_redis() -> AsyncRedis:
    """Singleton-Redis-Verbindung (pro Prozess EINE)."""
    global _pool
    if _pool is None:
        _pool = AsyncRedis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=10,
        )
    return _pool


async def close_redis() -> None:
    """Aufräumen beim Shutdown."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def push_task(project_id: str) -> None:
    """Legt einen Graph-Lauf in die Queue (Worker nimmt ihn entgegen)."""
    r = await get_redis()
    await r.lpush(QUEUE_KEY, project_id)
    logger.info("Task queued: %s", project_id)


async def pop_task(timeout: int = 5) -> str | None:
    """Worker: Nächsten Task aus der Queue nehmen (BLPOP, blockierend)."""
    r = await get_redis()
    result = await r.blpop(QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    return result[1]  # [key, value]


async def queue_contains(project_id: str) -> bool:
    """Prüfen, ob ein Projekt bereits in der Queue liegt (Duplikat-Schutz,
    z. B. für den Worker-Start-Scan nach einem Redeploy)."""
    r = await get_redis()
    entries = await r.lrange(QUEUE_KEY, 0, -1)
    return project_id in entries


async def publish_progress(project_id: str, event: str, data: dict) -> None:
    """Worker: Fortschritt an alle SSE-Listener publishen."""
    import json

    r = await get_redis()
    message = json.dumps({"event": event, **data}, ensure_ascii=False)
    await r.publish(progress_channel(project_id), message)


async def subscribe_progress(
    project_id: str, idle_timeout: float | None = None
) -> AsyncIterator[dict]:
    """SSE: Auf Fortschritts-Events für ein Projekt warten (Generator).

    `idle_timeout`: PubSub ist Fire-and-Forget — wer sich spät verbindet,
    verpasst Events (auch das terminal `done`/`error`). Nach `idle_timeout`
    Sekunden ohne Nachricht wird ein Sentinel-Event `{"event": "__idle__"}`
    geliefert, damit der Konsument Status/Heartbeat prüfen kann, statt
    ewig blind zu warten.
    """
    import json
    import time

    r = await get_redis()
    pubsub: PubSub = r.pubsub()
    await pubsub.subscribe(progress_channel(project_id))
    last_activity = time.monotonic()
    try:
        # Redis-PubSub ist push-basiert; wir wrappen in einen AsyncIterator
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=2.0
            )
            if message is None:
                if (
                    idle_timeout is not None
                    and (time.monotonic() - last_activity) >= idle_timeout
                ):
                    last_activity = time.monotonic()
                    yield {"event": "__idle__"}
                continue
            if message["type"] == "message":
                last_activity = time.monotonic()
                try:
                    yield json.loads(message["data"])
                except json.JSONDecodeError:
                    logger.warning("Ungültiges Progress-Event: %s", message["data"][:100])
    finally:
        await pubsub.unsubscribe(progress_channel(project_id))
        await pubsub.aclose()
