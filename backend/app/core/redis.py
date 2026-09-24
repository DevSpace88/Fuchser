"""
core/redis.py — Redis connection + queue/PubSub helpers
=======================================================

Architecture (reload-safe graph execution):
    1. API pushes project_id onto a Redis queue (LPUSH)
    2. Worker (separate process) takes tasks (BLPOP)
    3. Worker executes the graph and PUBLISHes progress
    4. SSE endpoint SUBSCRIBEs to the progress channel
    5. If SSE disconnects → the worker KEEPS RUNNING (Redis buffer)

Coolify-compatible: REDIS_URL env-var (e.g. redis://redis:6379/0).
"""

import logging
from collections.abc import AsyncIterator

from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.client import PubSub

from app.core.config import settings

logger = logging.getLogger(__name__)

# Queue for pending graph runs
QUEUE_KEY = "fuchser:queue"

# PubSub channel per project: fuchser:progress:{project_id}
def progress_channel(project_id: str) -> str:
    return f"fuchser:progress:{project_id}"


# ----------------------------------------------------------------------------
# Run marker (heartbeat): indicates that a task is currently active for a
# project (in the queue or in the worker). The API sets it when enqueuing
# (TTL 120s), the worker renews it every 15s (TTL 60s) — if the worker dies,
# the marker expires and the API can re-queue the run.
# ----------------------------------------------------------------------------
def run_marker_key(project_id: str) -> str:
    return f"fuchser:run:{project_id}"


async def set_run_marker(project_id: str, ttl: int = 120) -> None:
    """API: set the marker when enqueuing (long enough for the queue wait time)."""
    r = await get_redis()
    await r.set(run_marker_key(project_id), "1", ex=ttl)


async def refresh_run_marker(project_id: str, ttl: int = 60) -> None:
    """Worker: keep the marker alive during execution (heartbeat)."""
    r = await get_redis()
    await r.set(run_marker_key(project_id), "1", ex=ttl)


async def has_run_marker(project_id: str) -> bool:
    r = await get_redis()
    return bool(await r.exists(run_marker_key(project_id)))


async def clear_run_marker(project_id: str) -> None:
    """Worker: delete the marker after completion (done OR error)."""
    r = await get_redis()
    await r.delete(run_marker_key(project_id))


_pool: AsyncRedis | None = None


async def get_redis() -> AsyncRedis:
    """Singleton Redis connection (ONE per process)."""
    global _pool
    if _pool is None:
        _pool = AsyncRedis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=10,
        )
    return _pool


async def close_redis() -> None:
    """Cleanup on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def push_task(project_id: str) -> None:
    """Puts a graph run onto the queue (the worker picks it up)."""
    r = await get_redis()
    await r.lpush(QUEUE_KEY, project_id)
    logger.info("Task queued: %s", project_id)


async def pop_task(timeout: int = 5) -> str | None:
    """Worker: take the next task from the queue (BLPOP, blocking)."""
    r = await get_redis()
    result = await r.blpop(QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    return result[1]  # [key, value]


async def queue_contains(project_id: str) -> bool:
    """Check whether a project is already in the queue (duplicate protection,
    e.g. for the worker startup scan after a redeploy)."""
    r = await get_redis()
    entries = await r.lrange(QUEUE_KEY, 0, -1)
    return project_id in entries


async def publish_progress(project_id: str, event: str, data: dict) -> None:
    """Worker: publish progress to all SSE listeners."""
    import json

    r = await get_redis()
    message = json.dumps({"event": event, **data}, ensure_ascii=False)
    await r.publish(progress_channel(project_id), message)


async def subscribe_progress(
    project_id: str, idle_timeout: float | None = None
) -> AsyncIterator[dict]:
    """SSE: wait for progress events for a project (generator).

    `idle_timeout`: PubSub is fire-and-forget — whoever connects late
    misses events (including the terminal `done`/`error`). After
    `idle_timeout` seconds without a message, a sentinel event
    `{"event": "__idle__"}` is delivered so the consumer can check
    status/heartbeat instead of waiting blindly forever.
    """
    import json
    import time

    r = await get_redis()
    pubsub: PubSub = r.pubsub()
    await pubsub.subscribe(progress_channel(project_id))
    last_activity = time.monotonic()
    try:
        # Redis PubSub is push-based; we wrap it in an AsyncIterator
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
