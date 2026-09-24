"""
agent/usage.py — Token-Verbrauch aus LLM-Antworten extrahieren (Stufe 5)
==========================================================================

langchain-Modelle liefern in `response.usage_metadata` die Token-Zählung
des Providers: {"input_tokens": …, "output_tokens": …, "total_tokens": …}.

Wir sammeln sie in jedem Node als einzelnes Dict in den Reducer-Kanal
`usage` des States und summieren sie im Service für die Anzeige/Persistenz.
Fake-LLMs in Tests liefern oft KEIN usage_metadata — deshalb extrahieren
wir defensiv (Nullen statt Crash).
"""

from langchain_core.messages import AIMessage


def extract_usage(response: AIMessage) -> dict:
    """
    Liefert {input_tokens, output_tokens, total_tokens} — oder Nullen,
    wenn das Modell (z. B. ein Fake in Tests) nichts meldet.
    """
    meta = getattr(response, "usage_metadata", None) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0) or 0),
        "output_tokens": int(meta.get("output_tokens", 0) or 0),
        "total_tokens": int(meta.get("total_tokens", 0) or 0),
    }


def sum_usage(entries: list[dict]) -> dict:
    """Summiert eine Liste von extract_usage()-Dicts zu einem Gesamtwert."""
    return {
        "input_tokens": sum(e.get("input_tokens", 0) for e in entries),
        "output_tokens": sum(e.get("output_tokens", 0) for e in entries),
        "total_tokens": sum(e.get("total_tokens", 0) for e in entries),
        "llm_calls": len(entries),
    }
