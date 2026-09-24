"""
agent/graph.py — Der Multi-Agent-Graph (Stufe 4, PLAN.md)
==========================================================

LangGraph-Kernidee in 3 Sätzen:
    1. Ein Graph hat einen STATE (hier: TypedDict) — das gemeinsame
       "Gedächtnis", das durch die Nodes wandert.
    2. Nodes sind Funktionen `state -> partial state`: sie lesen den State
       und geben NUR die Felder zurück, die sie verändern.
    3. Edges verdrahten die Nodes; START/END sind Sentinel-Knoten.

Stufe 4 — der KRITIKER-LOOP (der Grund, warum es ein GRAPH ist):

                    START
                      │
              ┌───────▼────────┐
              │   SUPERVISOR   │  plant Sub-Fragen (JSON-planbar)
              └───────┬────────┘
                      │ conditional edge: FAN-OUT via Send
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   RESEARCHER     RESEARCHER   RESEARCHER     (parallel, je 1 Sub-Frage,
        └─────────────┼─────────────┘           ReAct-Loop mit Websuche)
                      │ (Join: Reducer mergt findings/sources)
              ┌───────▼────────┐
              │  SYNTHESIZER   │  Markdown-Report + Zitate, streamt Token
              └───────┬────────┘
                      ▼
              ┌───────────────┐   Lücken + Cap nicht erreicht?
              │    CRITIC     │ ──────────────────────────────┐
              └───────┬───────┘                               │
                      │ ok / Cap erreicht              Send pro Lücke
                      ▼                                       │
                     END  ◄─── (danach wieder Synthesizer) ◄──┘

Zwei Grenzen gegen Endlosigkeit: revision_count-Cap im State
(MAX_REVISIONS) und langgraphs globales recursion_limit als Netz.
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
# 1) STATE — das gemeinsame Gedächtnis des Graph-Laufs
# ----------------------------------------------------------------------------
class ResearchState(TypedDict):
    """
    Der globale State. findings/sources sind REDUCER-Kanäle (operator.add):
    Parallele Researcher-Instanzen liefern JEDE ihre Teilliste, und der
    Reducer fügt sie zusammen — statt dass die letzte Instanz die anderen
    überschreibt. Das ist DER Trick, der Multi-Agent in LangGraph trägt.

    critique/revision_count (Stufe 4): Bewertung des Kritikers und wie oft
    er schon eine Überarbeitungsrunde ausgelöst hat (Cap: MAX_REVISIONS).

    usage (Stufe 5): Token-Verbrauch je LLM-Call als Liste (Reducer) —
    der Service summiert für Anzeige/Persistenz.
    """

    question: str
    # Hochgeladene Dokumente ([{name, text}]) — der Researcher kann sie per
    # document_search-Tool durchsuchen (kein Prompt-Stuffing langer Texte).
    documents: list[dict]
    # Chat-Verlauf, den der Client mitgibt (Q/A-Paare, gekürzt). Supervisor
    # & Synthesizer bekommen ihn in den Prompt — "nochmal recherchieren"
    # funktioniert so auch nach fehlgeschlagenen Läufen.
    context_summary: str
    sub_questions: list[str]  # vom Supervisor geplant (kein Reducer: overwrite)
    findings: Annotated[list[Finding], operator.add]
    sources: Annotated[list[Source], operator.add]
    report: str | None
    critique: dict | None
    revision_count: int
    usage: Annotated[list[dict], operator.add]


# ----------------------------------------------------------------------------
# 2) FAN-OUTS / ROUTES — conditional edges als Send-Listen
# ----------------------------------------------------------------------------
def fan_out_researchers(state: ResearchState) -> list[Send]:
    """
    Hinter dem Supervisor: EIN Send pro Sub-Frage (der Start-Fan-out).

    Jeder Send trägt seinen eigenen Input-State (dict) mit — die Sub-Frage
    reist also IM Send-Objekt, nicht im globalen State. Der Cap ist
    Doppel-Sicherung (Prompt + Pydantic + hier).
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
    Hinter dem Kritiker: DER Loop der Stufe 4.

    Bei "gaps" UND Revisionen im Budget geht es mit Sends für die Lücken-
    Fragen zurück zu den Researchern (danach läuft die normale Kante
    researcher -> synthesizer wieder). Sonst: END.
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
# 3) GRAPH — Nodes + Edges, kompiliert (mit optionalem Checkpointer)
# ----------------------------------------------------------------------------
def build_research_graph(
    checkpointer=None,
    llm: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """
    Baut den Stufe-4-Graphen mit Kritiker-Loop.

    `checkpointer`:  persistence.get_checkpointer() → Threads in Postgres.
    `llm`:           Injektionsstelle für Tests (Fake-Chat-Modelle). None
                     heißt hier "Factory nutzen" — ohne Key Echo-Modus.
    """
    builder = StateGraph(ResearchState)
    # partial injiziert das (Fake-)LLM in jeden Node — dieselbe Technik,
    # mit der FastAPI Dependencies in Endpunkte spritzt.
    builder.add_node("supervisor", partial(supervisor_node, llm=llm))
    builder.add_node("researcher", partial(researcher_node, llm=llm))
    builder.add_node("synthesizer", partial(synthesizer_node, llm=llm))
    builder.add_node("critic", partial(critic_node, llm=llm))

    builder.add_edge(START, "supervisor")
    # Conditional edge statt normaler Kante: das ist das Fan-out.
    # Das dritte Argument listet die möglichen Ziel-Nodes (für die
    # Graph-Visualisierung/das Zeichnen des State-Diagramms).
    builder.add_conditional_edges("supervisor", fan_out_researchers, ["researcher"])
    # Join: feuert erst, wenn ALLE Researcher-Instanzen des Supersteps
    # fertig sind. Danach synthetisieren …
    builder.add_edge("researcher", "synthesizer")
    # … und der Kritiker entscheidet: END oder nochmal recherchieren.
    builder.add_edge("synthesizer", "critic")
    builder.add_conditional_edges("critic", route_after_critic, ["researcher", END])

    return builder.compile(checkpointer=checkpointer)
