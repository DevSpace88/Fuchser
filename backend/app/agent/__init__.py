"""
agent/ — Das Zuhause unseres LangGraph-"Deep Research"-Klon (siehe PLAN.md)

Aufbau (wächst mit den Stufen):
    llm.py          → DeepSeek-Bindung (ab Stufe 0)
    persistence.py  → Postgres-Checkpointer für Threads/State (ab Stufe 0)
    graph.py        → Graph-Definition (Stufe 0: Mini-Graph; Stufe 3: Multi-Agent)
"""
