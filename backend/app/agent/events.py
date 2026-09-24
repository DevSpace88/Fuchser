"""
agent/events.py — Custom-Stream-Events für die Live-Visualisierung (Stufe 4)
==============================================================================

LangGraph-Streams kennen mehrere Modi (messages/updates/custom). Für die
Graph-Ansicht im Frontend brauchen wir FEINGRANULARE Telemetrie:
" welcher Node läuft gerade? " — genau dafür ist der custom-Modus da.

Mechanik: Ein Node ruft get_stream_writer() und schreibt beliebige Dicts
in den Stream — sie landen beim Konsumenten (unserem SSE-Endpunkt) als
(mode="custom", payload)-Chunks.

Der try/except ist wichtig: get_stream_writer() funktioniert NUR innerhalb
eines laufenden Graph-Kontexts. Unit-Tests rufen Nodes direkt auf — ohne
Kontext darf emit() nicht crashen (einfach verwerfen).
"""

import logging

logger = logging.getLogger(__name__)


def emit(event: str, **data) -> None:
    """
    Schreibt ein Telemetrie-Event in den custom-Stream des Graph-Laufs.

    Beispiele:
        emit("node", node="supervisor", status="start")
        emit("node", node="critic", status="end", verdict="gaps")
    """
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        writer({"event": event, **data})
    except Exception:  # noqa: BLE001 — kein Graph-Kontext (z. B. Unit-Test)
        pass
