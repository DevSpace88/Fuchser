"""
agent/graph.py — the multi-agent graph (Stage 4, PLAN.md)
==========================================================

LangGraph's core idea in 3 sentences:
    1. A graph has a STATE (here: TypedDict) — the shared "memory"
       that travels through the nodes.
    2. Nodes are functions `state -> partial state`: they read the
       state and return ONLY the fields they change.
    3. Edges wire the nodes together; START/END are sentinel nodes.

Stage 4 — the CRITIC LOOP (the reason this is a GRAPH):

                    START
                      │
              ┌───────▼────────┐
              │   SUPERVISOR   │  plans sub-questions (JSON-plannable)
              └───────┬────────┘
                      │ conditional edge: FAN-OUT via Send
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   RESEARCHER     RESEARCHER   RESEARCHER     (parallel, 1 sub-question each,
        └─────────────┼─────────────┘           ReAct loop with web search)
                      │ (Join: reducer merges findings/sources)
              ┌───────▼────────┐
              │  SYNTHESIZER   │  Markdown report + citations, streams tokens
              └───────┬────────┘
                      ▼
              ┌───────────────┐   gaps + cap not reached?
              │    CRITIC     │ ──────────────────────────────┐
              └───────┬───────┘                               │
                      │ ok / cap reached              Send per gap
                      ▼                                       │
                     END  ◄─── (synthesizer again afterwards) ◄──┘

Two limits against endlessness: the revision_count cap in the state
(MAX_REVISIONS) and langgraph's global recursion_limit as a net.
"""

import logging
import operator
from functools import partial
from typing import Annotated, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from app.agent.events import emit
from app.agent.nodes.critic import MAX_REVISIONS, critic_node
from app.agent.nodes.researcher import Finding, researcher_node
from app.agent.nodes.supervisor import MAX_SUB_QUESTIONS, supervisor_node
from app.agent.nodes.synthesizer import synthesizer_node
from app.agent.tools import Source

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# 1) STATE — the shared memory of the graph run
# ----------------------------------------------------------------------------
class ResearchState(TypedDict):
    """
    The global state. findings/sources are REDUCER channels (operator.add):
    parallel researcher instances EACH deliver their partial list, and
    the reducer merges them — instead of the last instance overwriting
    the others. THIS is the trick that carries multi-agent in LangGraph.

    critique/revision_count (Stage 4): the critic's evaluation and how
    often it has already triggered a revision round (cap: MAX_REVISIONS).

    usage (Stage 5): token usage per LLM call as a list (reducer) —
    the service sums it for display/persistence.
    """

    question: str
    # Uploaded documents ([{name, text}]) — the researcher can search them
    # via the document_search tool (no prompt-stuffing of long texts).
    documents: list[dict]
    # Chat history the client passes along (Q/A pairs, truncated). Supervisor
    # & synthesizer get it in the prompt — this way "research again" keeps
    # working even after failed runs.
    context_summary: str
    sub_questions: list[str]  # planned by the supervisor (no reducer: overwrite)
    findings: Annotated[list[Finding], operator.add]
    sources: Annotated[list[Source], operator.add]
    report: str | None
    critique: dict | None
    revision_count: int
    usage: Annotated[list[dict], operator.add]


# ----------------------------------------------------------------------------
# 2) FAN-OUTS / ROUTES — conditional edges as Send lists
# ----------------------------------------------------------------------------
def fan_out_researchers(state: ResearchState) -> list[Send]:
    """
    Behind the supervisor: ONE Send per sub-question (the initial fan-out).

    Each Send carries its own input state (dict) — the sub-question
    travels IN the Send object, not in the global state. The cap is
    doubly secured (prompt + Pydantic + here).
    """
    emit("node", node="supervisor", status="end", planned=len(state.get("sub_questions", [])))
    return [
        Send(
            "researcher",
            {
                "question": state["question"],
                "sub_question": q,
                "documents": state.get("documents", []),
            },
        )
        for q in state.get("sub_questions", [])[:MAX_SUB_QUESTIONS]
    ]


def route_after_critic(state: ResearchState) -> list[Send] | str:
    """
    Behind the critic: THE Stage 4 loop.

    On "gaps" AND revisions within the budget, Sends for the gap
    questions go back to the researchers (afterwards the normal edge
    researcher -> synthesizer runs again). Otherwise: END.
    """
    critique = state.get("critique") or {}
    gaps = critique.get("gaps", [])
    revisions = state.get("revision_count", 0)
    if critique.get("verdict") == "gaps" and gaps and revisions <= MAX_REVISIONS:
        emit("node", node="critic", status="loop", gaps=gaps, revision=revisions)
        return [
            Send("researcher", {"question": state["question"], "sub_question": g}) for g in gaps
        ]
    return END


# ----------------------------------------------------------------------------
# 3) GRAPH — nodes + edges, compiled (with an optional checkpointer)
# ----------------------------------------------------------------------------
def build_research_graph(
    checkpointer=None,
    llm: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """
    Builds the Stage 4 graph with the critic loop.

    `checkpointer`:  persistence.get_checkpointer() → threads in Postgres.
    `llm`:           injection point for tests (fake chat models). None
                     here means "use the factory" — without a key, echo mode.
    """
    builder = StateGraph(ResearchState)
    # partial injects the (fake) LLM into every node — the same technique
    # FastAPI uses to inject dependencies into endpoints.
    builder.add_node("supervisor", partial(supervisor_node, llm=llm))
    builder.add_node("researcher", partial(researcher_node, llm=llm))
    builder.add_node("synthesizer", partial(synthesizer_node, llm=llm))
    builder.add_node("critic", partial(critic_node, llm=llm))

    builder.add_edge(START, "supervisor")
    # Conditional edge instead of a normal edge: this is the fan-out.
    # The third argument lists the possible target nodes (for the graph
    # visualization/drawing the state diagram).
    builder.add_conditional_edges("supervisor", fan_out_researchers, ["researcher"])
    # Join: fires only when ALL researcher instances of the superstep are
    # done. Then synthesize …
    builder.add_edge("researcher", "synthesizer")
    # … and the critic decides: END or research again.
    builder.add_edge("synthesizer", "critic")
    builder.add_conditional_edges("critic", route_after_critic, ["researcher", END])

    return builder.compile(checkpointer=checkpointer)
