"""
agent/nodes/ — the three nodes of the multi-agent graph (Stage 3, PLAN.md)

    supervisor.py   → plans 2–4 researchable sub-questions (structured output)
    researcher.py   → ReAct loop per sub-question (search → grading → answer)
    synthesizer.py  → writes the final report with source citations

graph.py wires them: START → supervisor → (Send fan-out) researcher*
→ synthesizer → END.
"""

from app.agent.nodes.researcher import researcher_node, run_react_loop
from app.agent.nodes.supervisor import supervisor_node
from app.agent.nodes.synthesizer import synthesizer_node

__all__ = ["researcher_node", "run_react_loop", "supervisor_node", "synthesizer_node"]
