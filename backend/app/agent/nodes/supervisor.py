"""
nodes/supervisor.py — THE PLANNER (Stage 3)
===========================================

The supervisor breaks the research question into 2–4 independently
researchable sub-questions.

Why HAND-CRAFTED JSON instead of llm.with_structured_output(ResearchPlan)?
    The elegant way (schema to the API -> guaranteed valid JSON) misbehaves
    in stream mode with the GLM coding endpoint: langchain-openai tries to
    parse the chunks INCREMENTALLY as JSON and crashes before the model
    even delivers JSON (PLAN.md §9, risk case 6 — it happened!).
    Solution: we ask for JSON in the PROMPT, extract the first {...}
    block and validate with Pydantic. Works with EVERY OpenAI-compatible
    provider — and also shows you what with_structured_output does for
    you internally.
"""

import logging
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.agent.events import emit
from app.agent.llm import get_llm
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

# Cap from PLAN.md §9: max. 4 parallel researchers (rate limits!).
MAX_SUB_QUESTIONS = 4


class ResearchPlan(BaseModel):
    """The structure the LLM MUST deliver for planning."""

    sub_questions: list[str] = Field(
        description="2 bis 4 konkrete, eigenständig recherchierbare Teilfragen",
        min_length=1,
        max_length=MAX_SUB_QUESTIONS,
    )


SUPERVISOR_SYSTEM = (
    "Du bist ein Recherche-Planer. Zerlege die Forschungsfrage in 2 bis "
    f"{MAX_SUB_QUESTIONS} konkrete, eigenständig recherchierbare Teilfragen, "
    "die zusammen die Frage vollständig abdecken. Jede Teilfrage muss für "
    "eine Websuche geeignet sein (spezifisch, keine Meta-Fragen)."
    '\n\nAntworte AUSSCHLIESSLICH mit JSON im Format: {"sub_questions": ["…", "…"]}'
)

# Finds the first {...} block (even across lines).
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_plan(text: str) -> ResearchPlan | None:
    """
    Extracts and validates the plan from the model response.

    Kept as a separate function so tests can feed it directly.
    """
    match = _JSON_BLOCK.search(text)
    if match is None:
        return None
    try:
        plan = ResearchPlan.model_validate_json(match.group(0))
    except ValidationError:
        return None
    return plan if plan.sub_questions else None


async def supervisor_node(state: dict, llm: BaseChatModel | None = None) -> dict:
    """
    Returns {'sub_questions': [...]} — the foundation for the Send fan-out.

    The cap (max. 4) is enforced TWICE: in the prompt AND in the
    Pydantic model (max_length) — never trust the AI at counting. ;)
    """
    question = state["question"]
    emit("node", node="supervisor", status="start")
    emit("tool", tool="plan", query=f"Plant: {question[:60]}", agent="supervisor")
    emit("tool", tool="plan", query=f"Plant Recherche zu: {question[:60]}", agent="supervisor")

    model = llm or get_llm()
    if model is None:
        # Echo mode: the question itself as the only "sub-question".
        return {"sub_questions": [question]}

    try:
        # Follow-up context (Stage 5): if this graph runs in the same thread
        # as an earlier research run, that run's findings are already in the
        # state — the supervisor then plans only the NEW aspects instead of
        # researching everything again. This is LangGraph thread memory.
        context_parts: list[str] = []

        # 0) Uploaded documents (document_search-capable)
        # 1) Explicit chat history from the client (also works after failed
        #    runs — the original context is never lost).
        chat_context = (state.get("context_summary") or "").strip()
        if chat_context:
            context_parts.append(f"Bisheriger Gesprächsverlauf mit dem User:\n{chat_context}")

        # 2) Findings from the thread (in case an earlier run succeeded).
        documents = state.get("documents") or []
        if documents:
            doc_names = ", ".join(d.get("name", "?") for d in documents)
            context_parts.append(
                f"Der User hat {len(documents)} Dokument(e) hochgeladen: {doc_names}. "
                "Die Researcher können sie mit dem document_search-Tool "
                "durchsuchen — plane Teilfragen, die diese Dokumente nutzen!"
            )

        prior = state.get("findings") or []
        if prior:
            summary = "\n".join(
                f"- {f.get('sub_question', '')}: {f.get('answer', '')[:200]}" for f in prior[-8:]
            )
            context_parts.append(f"Bisherige Erkenntnisse aus der vorherigen Recherche:\n{summary}")

        context = ""
        if context_parts:
            context = (
                "\n\n"
                + "\n\n".join(context_parts)
                + "\n\nDie aktuelle Frage bezieht sich darauf — plane NUR die "
                "Aspekte, die noch NICHT beantwortet wurden!"
            )

        response = await model.ainvoke(
            [
                SystemMessage(content=SUPERVISOR_SYSTEM),
                HumanMessage(content=f"Forschungsfrage: {question}{context}"),
            ]
        )
        plan = parse_plan(str(response.content))
        usage = [extract_usage(response)]
    except Exception:  # noqa: BLE001 — network/provider error -> fallback
        logger.warning(
            "Supervisor-Planung fehlgeschlagen — Fallback: Original-Frage",
            exc_info=True,
        )
        return {"sub_questions": [question]}

    if plan is None:
        # JSON unreadable/implausible -> fallback instead of a crash.
        logger.warning("Supervisor lieferte kein gültiges JSON — Fallback: Original-Frage")
        return {"sub_questions": [question]}

    sub_questions = [q.strip() for q in plan.sub_questions if q.strip()]
    return {
        "sub_questions": sub_questions[:MAX_SUB_QUESTIONS],
        "usage": usage,
    }
