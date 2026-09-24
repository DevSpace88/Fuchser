"""
agent/ — home of our LangGraph "Deep Research" clone (see PLAN.md)

Layout (grows with the stages):
    llm.py          → DeepSeek binding (from Stage 0)
    persistence.py  → Postgres checkpointer for threads/state (from Stage 0)
    graph.py        → graph definition (Stage 0: mini graph; Stage 3: multi-agent)
"""
