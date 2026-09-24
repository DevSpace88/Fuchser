"""
tests/test_agent_graph.py — Multi-agent graph (Stage 3, PLAN.md)
===============================================================

Tested WITHOUT network and WITHOUT an API key:
    * FakeMultiLLM plays the provider for ALL nodes: supervisor
      (structured output → ResearchPlan), researcher (ReAct loop with
      tool_calls) and synthesizer (echoes the prompt — so we can see
      that the findings really arrived).
    * search_web is replaced by a stub (deterministic sources).

Core questions of the tests:
    * Does the supervisor plan (with a cap)?
    * Fan-out: does ONE researcher run per sub-question (Send)?
    * Join: does the reducer merge findings/sources of all instances?
    * Does the synthesizer write a report from ALL findings?
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import build_research_graph, fan_out_researchers
from app.agent.nodes.researcher import researcher_node, run_react_loop
from app.agent.nodes.supervisor import MAX_SUB_QUESTIONS, supervisor_node
from app.agent.nodes.synthesizer import synthesizer_node
from app.agent.tools import Source


# ----------------------------------------------------------------------------
# Fixtures: fake LLM + mocked search
# ----------------------------------------------------------------------------
class FakeMultiLLM:
    """
    A fake for ALL roles — decides based on the message history:

      * with_structured_output (supervisor) -> JSON plan in prompt format
      * ReAct round without ToolMessage     -> request a tool call
      * prompt contains "Recherche-Ergebnisse" (synthesizer) -> echoes the prompt
      * otherwise                           -> "Antwort zu: <question>"
    """

    def __init__(self, critiques: list[str] | None = None):
        self.seen_prompts: list[str] = []
        # Critique responses as a queue (the last entry applies to all
        # further calls) — default: always satisfied.
        self.critiques = list(critiques or ['{"verdict": "ok"}'])

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.seen_prompts.append(str(messages))
        system_text = " ".join(str(m.content) for m in messages if m.type == "system")
        last_user = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        if "Qualitäts-Prüfer" in system_text:
            # Critic: JSON verdict from the queue (see critic.py).
            if len(self.critiques) > 1:
                return AIMessage(content=self.critiques.pop(0))
            return AIMessage(content=self.critiques[0])
        if "Recherche-Planer" in system_text:
            # Supervisor: JSON in prompt format (see supervisor.py).
            return AIMessage(
                content='{"sub_questions": '
                '["Was ist X?", "Wie nutzt man X?", "Welche Alternativen gibt es?"]}'
            )
        if "Recherche-Ergebnisse" in last_user:
            # Synthesizer (its prompt contains the merged findings).
            return AIMessage(content=f"REPORT::{last_user}")
        if not any(getattr(m, "tool_call_id", None) for m in messages):
            # First ReAct round: request a tool call (afterwards a ToolMessage
            # is in the history -> the next round delivers the answer).
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": last_user[:40]},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content=f"Antwort zu: {last_user}")


FAKE_SOURCES = [
    Source(title="Quelle A", url="https://example.com/a", snippet="AAA"),
    Source(title="Quelle B", url="https://example.com/b", snippet="BBB"),
]


async def _fake_search(query: str, max_results: int = 5) -> list[Source]:
    return FAKE_SOURCES


@pytest.fixture
def mocked_search(monkeypatch):
    # search_web runs in the researcher module — patch it there.
    import app.agent.nodes.researcher as researcher_mod

    monkeypatch.setattr(researcher_mod, "search_web", _fake_search)


# ============================================================================
# SUPERVISOR
# ============================================================================
@pytest.mark.asyncio
async def test_supervisor_plans_sub_questions():
    llm = FakeMultiLLM()
    result = await supervisor_node({"question": "Alles über LangGraph"}, llm=llm)
    assert len(result["sub_questions"]) == 3
    assert "Was ist X?" in result["sub_questions"]


@pytest.mark.asyncio
async def test_supervisor_fallback_on_invalid_json():
    class GarbageLLM:
        async def ainvoke(self, messages):
            return AIMessage(content="Ich kann kein JSON, sorry!")  # no {...}

    result = await supervisor_node({"question": "Frage?"}, llm=GarbageLLM())
    # Fallback: the original question as the only sub-question.
    assert result["sub_questions"] == ["Frage?"]


@pytest.mark.asyncio
async def test_supervisor_parse_plan_extracts_json_block():
    """parse_plan: extract JSON from surrounding text + validate it."""
    from app.agent.nodes.supervisor import parse_plan

    text = 'Gerne! Hier der Plan:\n```json\n{"sub_questions": ["A?", "B?"]}\n```'
    plan = parse_plan(text)
    assert plan is not None
    assert plan.sub_questions == ["A?", "B?"]
    assert parse_plan("überhaupt kein JSON") is None


# ============================================================================
# RESEARCHER (ReAct loop, one per sub-question)
# ============================================================================
@pytest.mark.asyncio
async def test_react_loop_searches_then_answers(mocked_search):
    llm = FakeMultiLLM()
    answer, sources, usage = await run_react_loop(llm, "Was sind Checkpointer?")
    assert "Antwort zu:" in answer
    assert sources == FAKE_SOURCES
    assert len(usage) == 2  # tool round + final answer = 2 LLM calls


@pytest.mark.asyncio
async def test_researcher_node_returns_reducer_channels(mocked_search):
    """The node writes ONLY to findings/sources — the basis for parallel fan-out."""
    llm = FakeMultiLLM()
    result = await researcher_node(
        {"question": "Alles über X", "sub_question": "Was ist X?"}, llm=llm
    )
    assert set(result.keys()) == {"findings", "sources", "usage"}
    assert result["findings"][0]["sub_question"] == "Was ist X?"
    assert result["sources"] == FAKE_SOURCES


@pytest.mark.asyncio
async def test_researcher_echo_mode(monkeypatch):
    import app.agent.nodes.researcher as researcher_mod

    monkeypatch.setattr(researcher_mod, "get_llm", lambda **_: None)
    result = await researcher_node({"question": "Q", "sub_question": "Sub"})
    assert result["findings"][0]["answer"] == "[Echo-Modus]"
    assert result["sources"] == []


# ============================================================================
# SYNTHESIZER
# ============================================================================
@pytest.mark.asyncio
async def test_synthesizer_dedupes_and_reports():
    llm = FakeMultiLLM()
    findings = [
        {"sub_question": "Was ist X?", "answer": "X ist …"},
        {"sub_question": "Wie X?", "answer": "So …"},
    ]
    doubled_sources = FAKE_SOURCES + FAKE_SOURCES  # duplicates!
    result = await synthesizer_node(
        {"question": "X?", "findings": findings, "sources": doubled_sources}, llm=llm
    )
    assert result["report"].startswith("REPORT::")
    assert "X ist …" in result["report"]  # findings were in the prompt
    # The synthesizer does NOT write sources back (reducer channel!
    # otherwise duplicates) — only the report.
    assert "sources" not in result


# ============================================================================
# THE WHOLE GRAPH: fan-out + join
# ============================================================================
@pytest.mark.asyncio
async def test_multi_agent_fan_out_and_join(mocked_search):
    """The core test: 3 planned sub-questions -> 3 researchers -> 1 report."""
    llm = FakeMultiLLM()
    g = build_research_graph(llm=llm)
    result = await g.ainvoke({"question": "Alles über LangGraph"})

    # Join: findings of all 3 instances merged (reducer!).
    sub_questions_found = {f["sub_question"] for f in result["findings"]}
    assert sub_questions_found == {"Was ist X?", "Wie nutzt man X?", "Welche Alternativen gibt es?"}

    # Sources: 3 researchers × 2 sources (stub).
    assert len(result["sources"]) == 6

    # Report from the synthesizer, with the findings folded in.
    assert result["report"].startswith("REPORT::")
    assert "Antwort zu:" in result["report"]


@pytest.mark.asyncio
async def test_fan_out_respects_cap():
    """More planned sub-questions than allowed? fan_out truncates to the cap."""
    state = {"question": "Q", "sub_questions": [f"Frage {i}" for i in range(10)]}
    sends = fan_out_researchers(state)
    assert len(sends) == MAX_SUB_QUESTIONS
    # Each Send carries ITS OWN sub-question in its individual input state.
    assert sends[0].arg["sub_question"] == "Frage 0"
    assert sends[1].arg["sub_question"] == "Frage 1"


@pytest.mark.asyncio
async def test_graph_echo_mode_full_run(monkeypatch):
    """Without an API key: supervisor->1 sub-question, echo findings, echo report."""
    import app.agent.llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_llm", lambda **_: None)
    g = build_research_graph()
    result = await g.ainvoke({"question": "Echo-Test?"})

    assert result["sub_questions"] == ["Echo-Test?"]
    assert result["findings"][0]["answer"] == "[Echo-Modus]"
    assert result["report"].startswith("## Echo-Report")


# ============================================================================
# CRITIC (Stage 4) — quality gate with gap loop
# ============================================================================
@pytest.mark.asyncio
async def test_critic_ok_verdict():
    from app.agent.nodes.critic import critic_node

    llm = FakeMultiLLM()  # default verdict: ok
    result = await critic_node(
        {"question": "Q", "report": "Guter Report.", "revision_count": 0}, llm=llm
    )
    assert result["critique"]["verdict"] == "ok"
    assert result["revision_count"] == 0  # no round triggered


@pytest.mark.asyncio
async def test_critic_gaps_increment_revision():
    from app.agent.nodes.critic import critic_node

    llm = FakeMultiLLM(critiques=['{"verdict": "gaps", "gaps": ["Was ist mit Y?"]}'])
    result = await critic_node(
        {"question": "Q", "report": "Lückenhaft.", "revision_count": 0}, llm=llm
    )
    assert result["critique"]["verdict"] == "gaps"
    assert result["critique"]["gaps"] == ["Was ist mit Y?"]
    assert result["revision_count"] == 1


@pytest.mark.asyncio
async def test_critic_caps_gap_count():
    from app.agent.nodes.critic import MAX_GAPS, critic_node

    llm = FakeMultiLLM(critiques=['{"verdict": "gaps", "gaps": ["A?", "B?", "C?", "D?"]}'])
    result = await critic_node({"question": "Q", "report": "R", "revision_count": 0}, llm=llm)
    assert len(result["critique"]["gaps"]) == MAX_GAPS  # 2, not 4


def test_route_after_critic_end_on_ok():
    from langgraph.graph import END as _END

    from app.agent.graph import route_after_critic

    state = {"question": "Q", "critique": {"verdict": "ok", "gaps": []}, "revision_count": 0}
    assert route_after_critic(state) == _END


def test_route_after_critic_sends_on_gaps():
    from app.agent.graph import route_after_critic

    state = {
        "question": "Q",
        "critique": {"verdict": "gaps", "gaps": ["Lücke 1?", "Lücke 2?"]},
        "revision_count": 1,  # below the cap -> one more round
    }
    sends = route_after_critic(state)
    assert all(s.node == "researcher" for s in sends)
    assert [s.arg["sub_question"] for s in sends] == ["Lücke 1?", "Lücke 2?"]


def test_route_after_critic_ends_at_cap():
    from langgraph.graph import END as _END

    from app.agent.graph import route_after_critic

    state = {
        "question": "Q",
        "critique": {"verdict": "gaps", "gaps": ["Nochmal?"]},
        "revision_count": 3,  # above MAX_REVISIONS -> terminate forcefully
    }
    assert route_after_critic(state) == _END


@pytest.mark.asyncio
async def test_full_loop_with_one_revision(mocked_search):
    """
    THE stage-4 core test: critic reports gaps -> researchers run for
    the gaps -> synthesizer rewrites -> critic is satisfied -> END.
    """
    llm = FakeMultiLLM(
        critiques=[
            '{"verdict": "gaps", "gaps": ["Was ist mit Preisen?"]}',  # 1st verdict
            '{"verdict": "ok"}',  # 2nd verdict (after the revision)
        ]
    )
    g = build_research_graph(llm=llm)
    result = await g.ainvoke({"question": "Alles über X"})

    # Initially 3 researchers + 1 gap researcher = 4 findings.
    assert len(result["findings"]) == 4
    assert result["findings"][3]["sub_question"] == "Was ist mit Preisen?"
    assert result["revision_count"] == 1
    assert result["critique"]["verdict"] == "ok"
    assert result["report"].startswith("REPORT::")


@pytest.mark.asyncio
async def test_loop_stops_at_revision_cap(mocked_search):
    """The critic is NEVER satisfied: after MAX_REVISIONS rounds it terminates
    forcefully."""
    from app.agent.nodes.critic import MAX_REVISIONS

    llm = FakeMultiLLM(critiques=['{"verdict": "gaps", "gaps": ["Noch was?"]}'])
    g = build_research_graph(llm=llm)
    result = await g.ainvoke({"question": "Endlos?"})

    # 3 initial researchers + MAX_REVISIONS gap researchers.
    assert len(result["findings"]) == 3 + MAX_REVISIONS
    # The counter ends at MAX+1: the final increment IS the signal
    # that routes to END (revisions <= MAX = one more round).
    assert result["revision_count"] == MAX_REVISIONS + 1


@pytest.mark.asyncio
async def test_synthesizer_strips_appended_sources_section():
    """User feedback: despite the prompt, the model likes to append a source
    list — the synthesizer cuts it off (the UI renders sources itself, with links)."""
    from langchain_core.messages import AIMessage

    from app.agent.nodes.synthesizer import strip_sources_section

    class StubbornLLM:
        async def ainvoke(self, messages):
            return AIMessage(
                content="## Bericht\n\nWichtiger Text [1] und [2].\n\n"
                "## Quellen\n\n1. https://example.com/a\n2. https://example.com/b"
            )

    findings = [{"sub_question": "F?", "answer": "A"}]
    result = await synthesizer_node(
        {"question": "Q", "findings": findings, "sources": []}, llm=StubbornLLM()
    )
    assert "## Quellen" not in result["report"]
    assert "Wichtiger Text [1] und [2]." in result["report"]

    # Direct unit test of the strip function (also for the bibliography heading):
    assert "Quellen" not in strip_sources_section("Text\n\n### Literaturverzeichnis\n\n1. x")
    assert strip_sources_section("Text ohne Anhang").startswith("Text")


def test_strip_plain_text_sources_block():
    """User-feedback variant: sources block WITHOUT a Markdown heading —
    just the plain-text line 'Quellen'. Must also go (the UI renders it itself)."""
    from app.agent.nodes.synthesizer import strip_sources_section

    report = (
        "## Bericht\n\nEinleitung [1].\n\nAbschnitt mit Fakten [2][3].\n\n"
        "Hinweis zu Lücken: etwas unvollständig.\n\n"
        "Quellen\n"
        "BMF-Schreiben vom 15.10.2024. https://www.bundesfinanzministerium.de/x.pdf\n"
        "IHK Frankfurt. https://www.frankfurt-main.ihk.de/y\n"
        "Rödl & Partner. https://www.roedl.com/z\n"
    )
    result = strip_sources_section(report)
    assert "Quellen" not in result
    assert "https://" not in result
    assert "Hinweis zu Lücken" in result  # content stays, only the dump is removed


def test_strip_does_not_cut_legit_sentence_with_quellen():
    """A sentence like 'Die Quellen sind vielfältig' must NOT be cut off —
    the plain-text strip requires 2+ URLs after it."""
    from app.agent.nodes.synthesizer import strip_sources_section

    report = "## Bericht\n\nDie Quellen sind vielfältig [1]. Mehr Text folgt."
    assert strip_sources_section(report) == report


@pytest.mark.asyncio
async def test_supervisor_uses_chat_context():
    """Stage 5+: the chat history (context_summary) lands in the supervisor
    prompt — even when the thread has NO findings (e.g. after errors)."""
    llm = FakeMultiLLM()
    state = {
        "question": "recherchiere das nochmal bitte",
        "context_summary": (
            "User: Wie hoch ist das Gehalt bei Laravel- vs FastAPI-Entwicklern?\n"
            "Fuchser: (fehlgeschlagen: Rate-Limit)"
        ),
        # NO findings — exactly the failure case from the real chat.
    }
    result = await supervisor_node(state, llm=llm)
    assert result["sub_questions"]  # plans anyway
    # The prompt (in seen_prompts[0]) contains the history.
    assert "Laravel" in llm.seen_prompts[0]
    assert "Rate-Limit" in llm.seen_prompts[0]
