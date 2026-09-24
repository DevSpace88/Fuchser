"""
tests/test_agent_graph.py — Multi-Agent-Graph (Stufe 3, PLAN.md)
=================================================================

Getestet OHNE Netzwerk und OHNE API-Key:
    * FakeMultiLLM spielt den Provider für ALLE Nodes: Supervisor
      (structured output → ResearchPlan), Researcher (ReAct-Loop mit
      tool_calls) und Synthesizer (echo't den Prompt — so sehen wir,
      dass die Findings wirklich angekommen sind).
    * search_web wird durch einen Stub ersetzt (deterministische Quellen).

Kernfragen der Tests:
    * Plant der Supervisor (mit Cap)?
    * Fan-out: Läuft EIN Researcher pro Sub-Frage (Send)?
    * Join: Mergt der Reducer findings/sources aller Instanzen?
    * Schreibt der Synthesizer einen Report aus ALLEN Findings?
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import build_research_graph, fan_out_researchers
from app.agent.nodes.researcher import researcher_node, run_react_loop
from app.agent.nodes.supervisor import MAX_SUB_QUESTIONS, supervisor_node
from app.agent.nodes.synthesizer import synthesizer_node
from app.agent.tools import Source


# ----------------------------------------------------------------------------
# Fixtures: Fake-LLM + gemockte Suche
# ----------------------------------------------------------------------------
class FakeMultiLLM:
    """
    Ein Fake für ALLE Rollen — entscheidet anhand der Nachrichtenlage:

      * with_structured_output (Supervisor) -> JSON-Plan im Prompt-Format
      * ReAct-Runde ohne ToolMessage        -> Tool-Call wünschen
      * Prompt enthält "Recherche-Ergebnisse" (Synthesizer) -> echo't den Prompt
      * sonst                               -> "Antwort zu: <Frage>"
    """

    def __init__(self, critiques: list[str] | None = None):
        self.seen_prompts: list[str] = []
        # Kritiker-Antworten als Queue (letzter Eintrag gilt für alle
        # weiteren Aufrufe) — Default: immer zufrieden.
        self.critiques = list(critiques or ['{"verdict": "ok"}'])

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.seen_prompts.append(str(messages))
        system_text = " ".join(str(m.content) for m in messages if m.type == "system")
        last_user = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        if "Qualitäts-Prüfer" in system_text:
            # Kritiker: JSON-Urteil aus der Queue (siehe critic.py).
            if len(self.critiques) > 1:
                return AIMessage(content=self.critiques.pop(0))
            return AIMessage(content=self.critiques[0])
        if "Recherche-Planer" in system_text:
            # Supervisor: JSON im Prompt-Format (siehe supervisor.py).
            return AIMessage(
                content='{"sub_questions": '
                '["Was ist X?", "Wie nutzt man X?", "Welche Alternativen gibt es?"]}'
            )
        if "Recherche-Ergebnisse" in last_user:
            # Synthesizer (sein Prompt enthält die gemergten Findings).
            return AIMessage(content=f"REPORT::{last_user}")
        if not any(getattr(m, "tool_call_id", None) for m in messages):
            # Erste ReAct-Runde: Tool wünschen (danach steht ein ToolMessage
            # in der Historie -> nächste Runde liefert die Antwort).
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
    # search_web wird im researcher-Modul ausgeführt — dort patchen.
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
            return AIMessage(content="Ich kann kein JSON, sorry!")  # kein {...}

    result = await supervisor_node({"question": "Frage?"}, llm=GarbageLLM())
    # Fallback: Original-Frage als einzige Sub-Frage.
    assert result["sub_questions"] == ["Frage?"]


@pytest.mark.asyncio
async def test_supervisor_parse_plan_extracts_json_block():
    """parse_plan: JSON aus umgebendem Text extrahieren + validieren."""
    from app.agent.nodes.supervisor import parse_plan

    text = 'Gerne! Hier der Plan:\n```json\n{"sub_questions": ["A?", "B?"]}\n```'
    plan = parse_plan(text)
    assert plan is not None
    assert plan.sub_questions == ["A?", "B?"]
    assert parse_plan("überhaupt kein JSON") is None


# ============================================================================
# RESEARCHER (ReAct-Loop, pro Sub-Frage)
# ============================================================================
@pytest.mark.asyncio
async def test_react_loop_searches_then_answers(mocked_search):
    llm = FakeMultiLLM()
    answer, sources, usage = await run_react_loop(llm, "Was sind Checkpointer?")
    assert "Antwort zu:" in answer
    assert sources == FAKE_SOURCES
    assert len(usage) == 2  # Tool-Runde + finale Antwort = 2 LLM-Calls


@pytest.mark.asyncio
async def test_researcher_node_returns_reducer_channels(mocked_search):
    """Der Node schreibt NUR in findings/sources — Basis fürs parallele Fan-out."""
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
    doubled_sources = FAKE_SOURCES + FAKE_SOURCES  # Duplikate!
    result = await synthesizer_node(
        {"question": "X?", "findings": findings, "sources": doubled_sources}, llm=llm
    )
    assert result["report"].startswith("REPORT::")
    assert "X ist …" in result["report"]  # Findings waren im Prompt
    # Der Synthesizer schreibt KEINE sources zurück (Reducer-Kanal! sonst
    # Duplikate) — nur der Report.
    assert "sources" not in result


# ============================================================================
# DER GANZE GRAPH: Fan-out + Join
# ============================================================================
@pytest.mark.asyncio
async def test_multi_agent_fan_out_and_join(mocked_search):
    """Der Kern-Test: 3 geplante Sub-Fragen -> 3 Researcher -> 1 Report."""
    llm = FakeMultiLLM()
    g = build_research_graph(llm=llm)
    result = await g.ainvoke({"question": "Alles über LangGraph"})

    # Join: findings aller 3 Instanzen gemergt (Reducer!).
    sub_questions_found = {f["sub_question"] for f in result["findings"]}
    assert sub_questions_found == {"Was ist X?", "Wie nutzt man X?", "Welche Alternativen gibt es?"}

    # Quellen: 3 Researcher × 2 Quellen (Stub).
    assert len(result["sources"]) == 6

    # Report aus dem Synthesizer, mit eingeflossenen Findings.
    assert result["report"].startswith("REPORT::")
    assert "Antwort zu:" in result["report"]


@pytest.mark.asyncio
async def test_fan_out_respects_cap():
    """Mehr geplante Sub-Fragen als erlaubt? fan_out schneidet auf den Cap ab."""
    state = {"question": "Q", "sub_questions": [f"Frage {i}" for i in range(10)]}
    sends = fan_out_researchers(state)
    assert len(sends) == MAX_SUB_QUESTIONS
    # Jeder Send trägt SEINE Sub-Frage im eigenen Input-State.
    assert sends[0].arg["sub_question"] == "Frage 0"
    assert sends[1].arg["sub_question"] == "Frage 1"


@pytest.mark.asyncio
async def test_graph_echo_mode_full_run(monkeypatch):
    """Ohne API-Key: Supervisor->1 Sub-Frage, Echo-Findings, Echo-Report."""
    import app.agent.llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_llm", lambda **_: None)
    g = build_research_graph()
    result = await g.ainvoke({"question": "Echo-Test?"})

    assert result["sub_questions"] == ["Echo-Test?"]
    assert result["findings"][0]["answer"] == "[Echo-Modus]"
    assert result["report"].startswith("## Echo-Report")


# ============================================================================
# CRITIC (Stufe 4) — Qualitäts-Gate mit Lücken-Loop
# ============================================================================
@pytest.mark.asyncio
async def test_critic_ok_verdict():
    from app.agent.nodes.critic import critic_node

    llm = FakeMultiLLM()  # Default-Urteil: ok
    result = await critic_node(
        {"question": "Q", "report": "Guter Report.", "revision_count": 0}, llm=llm
    )
    assert result["critique"]["verdict"] == "ok"
    assert result["revision_count"] == 0  # keine Runde ausgelöst


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
    assert len(result["critique"]["gaps"]) == MAX_GAPS  # 2, nicht 4


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
        "revision_count": 1,  # unter dem Cap -> noch eine Runde
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
        "revision_count": 3,  # über MAX_REVISIONS -> hart beenden
    }
    assert route_after_critic(state) == _END


@pytest.mark.asyncio
async def test_full_loop_with_one_revision(mocked_search):
    """
    DER Stufe-4-Kern-Test: Kritiker meldet Lücken -> Researcher laufen für
    die Lücken -> Synthesizer schreibt neu -> Kritiker ist zufrieden -> END.
    """
    llm = FakeMultiLLM(
        critiques=[
            '{"verdict": "gaps", "gaps": ["Was ist mit Preisen?"]}',  # 1. Urteil
            '{"verdict": "ok"}',  # 2. Urteil (nach der Überarbeitung)
        ]
    )
    g = build_research_graph(llm=llm)
    result = await g.ainvoke({"question": "Alles über X"})

    # Anfangs 3 Researcher + 1 Lücken-Researcher = 4 Findings.
    assert len(result["findings"]) == 4
    assert result["findings"][3]["sub_question"] == "Was ist mit Preisen?"
    assert result["revision_count"] == 1
    assert result["critique"]["verdict"] == "ok"
    assert result["report"].startswith("REPORT::")


@pytest.mark.asyncio
async def test_loop_stops_at_revision_cap(mocked_search):
    """Kritiker ist NIE zufrieden: nach MAX_REVISIONS Runden wird hart beendet."""
    from app.agent.nodes.critic import MAX_REVISIONS

    llm = FakeMultiLLM(critiques=['{"verdict": "gaps", "gaps": ["Noch was?"]}'])
    g = build_research_graph(llm=llm)
    result = await g.ainvoke({"question": "Endlos?"})

    # 3 Anfangs-Researcher + MAX_REVISIONS Lücken-Researcher.
    assert len(result["findings"]) == 3 + MAX_REVISIONS
    # Der Zähler endet bei MAX+1: die letzte Inkrementierung IST das Signal,
    # das die Route zum END schickt (revisions <= MAX = noch eine Runde).
    assert result["revision_count"] == MAX_REVISIONS + 1


@pytest.mark.asyncio
async def test_synthesizer_strips_appended_sources_section():
    """User-Feedback: Modell hängt trotz Prompt gern eine Quellenliste an —
    der Synthesizer schneidet sie ab (die UI rendert Quellen selbst, mit Links)."""
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

    # Direkter Einheitstest der Strip-Funktion (auch Literaturverzeichnis):
    assert "Quellen" not in strip_sources_section("Text\n\n### Literaturverzeichnis\n\n1. x")
    assert strip_sources_section("Text ohne Anhang").startswith("Text")


def test_strip_plain_text_sources_block():
    """User-Feedback-Variante: Quellen-Block OHNE Markdown-Überschrift —
    nur die Pure-Text-Zeile 'Quellen'. Muss auch weg (UI rendert selbst)."""
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
    assert "Hinweis zu Lücken" in result  # Inhalt bleibt, nur der Dump fliegt


def test_strip_does_not_cut_legit_sentence_with_quellen():
    """Ein Satz wie 'Die Quellen sind vielfältig' darf NICHT abgeschnitten
    werden — der Plain-Text-Strip verlangt 2+ URLs dahinter."""
    from app.agent.nodes.synthesizer import strip_sources_section

    report = "## Bericht\n\nDie Quellen sind vielfältig [1]. Mehr Text folgt."
    assert strip_sources_section(report) == report


@pytest.mark.asyncio
async def test_supervisor_uses_chat_context():
    """Stufe 5+: Der Chat-Verlauf (context_summary) landet im Supervisor-
    Prompt — auch wenn der Thread KEINE findings hat (z. B. nach Fehlern)."""
    llm = FakeMultiLLM()
    state = {
        "question": "recherchiere das nochmal bitte",
        "context_summary": (
            "User: Wie hoch ist das Gehalt bei Laravel- vs FastAPI-Entwicklern?\n"
            "Fuchser: (fehlgeschlagen: Rate-Limit)"
        ),
        # KEINE findings — genau der Fehlerfall aus dem echten Chat.
    }
    result = await supervisor_node(state, llm=llm)
    assert result["sub_questions"]  # plant trotzdem
    # Der Prompt (in seen_prompts[0]) enthält den Verlauf.
    assert "Laravel" in llm.seen_prompts[0]
    assert "Rate-Limit" in llm.seen_prompts[0]
