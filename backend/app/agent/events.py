"""
agent/events.py — custom stream events for the live visualization (Stage 4)
==============================================================================

LangGraph streams come in several modes (messages/updates/custom). For the
graph view in the frontend we need FINE-GRAINED telemetry:
" which node is running right now? " — the custom mode exists for exactly
that.

Mechanics: a node calls get_stream_writer() and writes arbitrary dicts
into the stream — they arrive at the consumer (our SSE endpoint) as
(mode="custom", payload) chunks.

The try/except matters: get_stream_writer() only works INSIDE a running
graph context. Unit tests call nodes directly — without a context,
emit() must not crash (just discard).
"""

import logging

logger = logging.getLogger(__name__)


def emit(event: str, **data) -> None:
    """
    Writes a telemetry event into the graph run's custom stream.

    Examples:
        emit("node", node="supervisor", status="start")
        emit("node", node="critic", status="end", verdict="gaps")
    """
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        writer({"event": event, **data})
    except Exception:  # noqa: BLE001 — kein Graph-Kontext (z. B. Unit-Test)
        pass
