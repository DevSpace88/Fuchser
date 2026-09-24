"""
agent/nodes/ — die drei Nodes des Multi-Agent-Graphen (Stufe 3, PLAN.md)

    supervisor.py   → plant 2–4 recherchierbare Sub-Fragen (structured output)
    researcher.py   → ReAct-Loop pro Sub-Frage (Suche → Grading → Antwort)
    synthesizer.py  → schreibt den finalen Report mit Quellen-Zitaten

graph.py verdrahtet sie: START → supervisor → (Send-Fan-out) researcher*
→ synthesizer → END.
"""

from app.agent.nodes.researcher import researcher_node, run_react_loop
from app.agent.nodes.supervisor import supervisor_node
from app.agent.nodes.synthesizer import synthesizer_node

__all__ = ["researcher_node", "run_react_loop", "supervisor_node", "synthesizer_node"]
