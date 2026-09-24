"""
nodes/critic.py — THE CRITIC (Stage 4): quality gate with a gap loop
=========================================================================

The critic evaluates the report: does it answer the research question
completely? Two possible verdicts (like the supervisor: prompt-based
JSON + Pydantic validation — GLM-safe, see supervisor.py):

    {"verdict": "ok"}                              -> graph ends.
    {"verdict": "gaps", "gaps": ["…", "…"]}        -> gaps! The route
       after the critic starts researchers ONLY for the gap questions,
       the synthesizer writes a revised version — at most
       MAX_REVISIONS times, otherwise it aborts (endless-loop protection).

This is THE showcase for conditional edges with a loop — the reason
this thing is called a "graph" and not a "pipeline".
"""

import logging
import re
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from app.agent.events import emit
from app.agent.llm import get_llm
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

# How many revision rounds may the critic trigger at most?
MAX_REVISIONS = 2
# At most this many gap questions added per round:
MAX_GAPS = 2

# Report truncation for the prompt (saving tokens — the critic doesn't
# need it word for word, just structure + coverage).
_REPORT_PREVIEW_CHARS = 6000


class Critique(BaseModel):
    """Evaluation of the report by the critic."""

    verdict: Literal["ok", "gaps"]
    gaps: list[str] = Field(
        default_factory=list,
        description="bei verdict=gaps: 1–2 neue, konkrete Teilfragen",
    )


CRITIC_SYSTEM = (
    "Du bist ein kritischer Qualitäts-Prüfer von Recherche-Reports. "
    "Prüfe, ob der Report die Forschungsfrage vollständig und mit "
    "ausreichender Tiefe beantwortet.\n\n"
    'Antworte AUSSCHLIESSLICH mit JSON: {"verdict": "ok"} '
    'oder {"verdict": "gaps", "gaps": ["…", "…"]}. '
    f"Höchstens {MAX_GAPS} Lücken-Fragen. Sei nicht überkritisch: "
    "Wenn der Report die Frage gut abdeckt, ist ok die richtige Antwort."
)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_critique(text: str) -> Critique | None:
    """Extracts/validates the evaluation (analogous to parse_plan in the supervisor)."""
    match = _JSON_BLOCK.search(text)
    if match is None:
        return None
    try:
        critique = Critique.model_validate_json(match.group(0))
    except ValidationError:
        return None
    return critique


async def critic_node(state: dict, llm: BaseChatModel | None = None) -> dict:
    """
    Evaluates the report → {'critique': {...}} (+ revision_count on gaps).

    revision_count is only incremented on verdict=gaps — the route
    afterwards decides from the counter whether another round runs
    (<= cap) or whether things end hard.
    """
    emit("node", node="critic", status="start")

    question = state["question"]
    report = state.get("report") or ""
    revision_count = state.get("revision_count", 0)

    model = llm or get_llm()
    critique: Critique | None = None
    usage: list[dict] = []

    if model is None:
        critique = Critique(verdict="ok")  # echo mode: always satisfied
    else:
        try:
            response = await model.ainvoke(
                [
                    SystemMessage(content=CRITIC_SYSTEM),
                    HumanMessage(
                        content=(
                            f"Forschungsfrage: {question}\n\n"
                            f"Report:\n{report[:_REPORT_PREVIEW_CHARS]}"
                        )
                    ),
                ]
            )
            critique = parse_critique(str(response.content))
            usage.append(extract_usage(response))
        except Exception:  # noqa: BLE001 — provider error -> ok (don't block)
            logger.warning("Kritiker-Call fehlgeschlagen — bewerte als ok", exc_info=True)

    if critique is None or critique.verdict not in ("ok", "gaps"):
        critique = Critique(verdict="ok")  # unreadable -> don't block

    new_revision_count = revision_count
    if critique.verdict == "gaps":
        # Sanitize the gaps (cap + drop empty strings).
        critique.gaps = [g.strip() for g in critique.gaps if g.strip()][:MAX_GAPS]
        if not critique.gaps:
            critique.verdict = "ok"  # "gaps" without gap questions doesn't count
        else:
            new_revision_count = revision_count + 1

    emit(
        "node",
        node="critic",
        status="end",
        verdict=critique.verdict,
        gaps=critique.gaps,
        revision=new_revision_count,
    )
    return {
        "critique": critique.model_dump(),
        "revision_count": new_revision_count,
        "usage": usage,
    }
