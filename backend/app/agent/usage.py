"""
agent/usage.py — extract token usage from LLM responses (Stage 5)
==========================================================================

langchain models deliver the provider's token counts in
`response.usage_metadata`: {"input_tokens": …, "output_tokens": …,
"total_tokens": …}.

We collect them in every node as individual dicts in the state's `usage`
reducer channel and sum them in the service for display/persistence.
Fake LLMs in tests often deliver NO usage_metadata — that's why we
extract defensively (zeros instead of a crash).
"""

from langchain_core.messages import AIMessage


def extract_usage(response: AIMessage) -> dict:
    """
    Returns {input_tokens, output_tokens, total_tokens} — or zeros if
    the model (e.g. a fake in tests) reports nothing.
    """
    meta = getattr(response, "usage_metadata", None) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0) or 0),
        "output_tokens": int(meta.get("output_tokens", 0) or 0),
        "total_tokens": int(meta.get("total_tokens", 0) or 0),
    }


def sum_usage(entries: list[dict]) -> dict:
    """Sums a list of extract_usage() dicts into a single total."""
    return {
        "input_tokens": sum(e.get("input_tokens", 0) for e in entries),
        "output_tokens": sum(e.get("output_tokens", 0) for e in entries),
        "total_tokens": sum(e.get("total_tokens", 0) for e in entries),
        "llm_calls": len(entries),
    }
