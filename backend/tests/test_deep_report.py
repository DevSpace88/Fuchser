"""
tests/test_deep_report.py — Deep-Report-Pipeline (Phase 2)
===========================================================
Outline-Planung, Kapitel-Recherche (Fan-out), sequenzielle Kapitel-Autoren,
Assembly mit Inhaltsverzeichnis + Literaturverzeichnis (APA/IEEE).
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.citations import format_bibliography
from app.agent.deep_report import (
    _parse_outline_json,
    build_deep_report_graph,
    chapter_writer_node,
    clean_chapter_content,
    outline_node,
)


def test_clean_chapter_content():
    # 1) Reines Markdown-Heading mit demselben Titel
    raw1 = "## US-Konzerne und Scale-ups\n\nErster Absatz mit Text."
    assert clean_chapter_content(raw1, "US-Konzerne und Scale-ups") == "Erster Absatz mit Text."

    # 2) Heading mit Nummerierung
    raw2 = "### 3. US-Konzerne und Scale-ups\n\nErster Absatz."
    assert clean_chapter_content(raw2, "US-Konzerne und Scale-ups") == "Erster Absatz."

    # 3) Fettgedruckte Überschrift
    raw3 = "**US-Konzerne und Scale-ups**\n\nErster Absatz."
    assert clean_chapter_content(raw3, "US-Konzerne und Scale-ups") == "Erster Absatz."

    # 4) Unterabschnitt (3.1 o.ä.) darf NICHT gelöscht werden
    raw4 = "### 3.1 Einführung\n\nErster Absatz."
    assert clean_chapter_content(raw4, "US-Konzerne und Scale-ups") == raw4.strip()

    # 5) Normaler Fließtext ohne Überschrift bleibt erhalten
    raw5 = "In diesem Kapitel untersuchen wir US-Konzerne."
    assert clean_chapter_content(raw5, "US-Konzerne und Scale-ups") == raw5


def test_parse_outline_json_valid():
    text = 'Gerne! ```json\n{"title": "T", "abstract": "A", "chapters": [{"title": "K1", "focus": "F1"}, {"title": "K2", "focus": "F2"}, {"title": "K3", "focus": "F3"}]}\n```'
    outline = _parse_outline_json(text)
    assert outline is not None
    assert [c["title"] for c in outline["chapters"]] == ["K1", "K2", "K3"]


def test_parse_outline_json_invalid():
    assert _parse_outline_json("kein json") is None
    assert _parse_outline_json('{"chapters": []}') is None


def test_bibliography_styles():
    sources = [{"title": "LangGraph Doku", "url": "https://docs.langchain.com/x/2024/guide"}]
    apa = format_bibliography(sources, "apa")
    ieee = format_bibliography(sources, "ieee")
    assert "docs.langchain.com" in apa and "*" in apa  # institutioneller Autor + Kursiv
    assert "[1]" in ieee and "Verfügbar:" in ieee


class DeepFakeLLM:
    """Antwortet je Prompt-Inhalt: Outline-JSON, ReAct-Toolcall/Final, Kapiteltext."""

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        text = str(messages)
        last_user = next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        if "Outline-Planer" in text:
            return AIMessage(
                content=(
                    '{"title": "FastAPI Deep", "abstract": "Alles über FastAPI.", '
                    '"chapters": [{"title": "Grundlagen", "focus": "Basics"}, '
                    '{"title": "Architektur", "focus": "Schichten"}, '
                    '{"title": "Fazit", "focus": "Zusammen"}]}'
                )
            )
        if any(getattr(m, "tool_call_id", None) for m in messages):
            return AIMessage(content=f"Recherche zu: {last_user[:40]}")
        if "Fachautor" in text:
            return AIMessage(
                content=f"### Unterabschnitt\n\nInhalt zu {last_user.split(chr(10))[0][:30]} mit Zitat [1]. "
                * 30
            )
        # ReAct-Runde 1: Tool wünschen
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "web_search",
                    "args": {"query": last_user[:30]},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        )


@pytest.mark.asyncio
async def test_outline_node_emits_and_parses():
    llm = DeepFakeLLM()
    result = await outline_node({"question": "FastAPI?"}, llm=llm)
    assert len(result["outline"]["chapters"]) == 3
    assert result["outline"]["title"] == "FastAPI Deep"


@pytest.mark.asyncio
async def test_deep_graph_full_run(monkeypatch):
    """Outline -> 3 Kapitel-Recherchen (fan-out) -> Writer -> Assembly."""
    import app.agent.nodes.researcher as researcher_mod

    async def fake_search(query, max_results=5):
        return [
            {
                "title": f"Q {query[:10]}",
                "url": f"https://x/{abs(hash(query)) % 1000}",
                "snippet": "s",
            }
        ]

    monkeypatch.setattr(researcher_mod, "search_web", fake_search)

    llm = DeepFakeLLM()
    graph = build_deep_report_graph(llm=llm)
    result = await graph.ainvoke(
        {
            "question": "FastAPI-Tiefenanalyse",
            "documents": [],
            "context_summary": "",
            "citation_style": "ieee",
        }
    )

    # 3 Kapitel geschrieben, mit Roem-Faden (sequenziell)
    assert [c["title"] for c in result["chapters_written"]] == [
        "Grundlagen",
        "Architektur",
        "Fazit",
    ]
    # Assembly: TOC + nummerierte Kapitel + Literaturverzeichnis
    report = result["report"]
    assert "## Inhaltsverzeichnis" in report
    assert "## 1. Grundlagen" in report and "## 3. Fazit" in report
    assert "Literaturverzeichnis" in report
    # Usage aus outline + researcher + writer
    assert len(result["usage"]) >= 5


@pytest.mark.asyncio
async def test_writer_echo_mode():
    state = {
        "question": "Q",
        "outline": {"title": "T", "abstract": "", "chapters": [{"title": "A", "focus": "x"}]},
        "chapter_findings": [],
        "citation_style": "apa",
    }
    result = await chapter_writer_node(state, llm=None)
    assert "[Echo-Modus]" in result["report"]
    assert "Literaturverzeichnis" in result["report"]


@pytest.mark.asyncio
async def test_writer_resume_skips_written_chapters():
    """RESUME: bereits geschriebene Kapitel werden übersprungen, nicht neu geschrieben."""
    llm = DeepFakeLLM()
    state = {
        "question": "FastAPI?",
        "outline": {
            "title": "T",
            "abstract": "",
            "chapters": [
                {"title": "Grundlagen", "focus": "x"},
                {"title": "Architektur", "focus": "y"},
                {"title": "Fazit", "focus": "z"},
            ],
        },
        "chapter_findings": [],
        "citation_style": "ieee",
        "chapters_written": [{"title": "Grundlagen", "content": "### Alt\n\nBereits geschrieben."}],
    }
    result = await chapter_writer_node(state, llm=llm)
    # Reihenfolge entspricht der Gliederung; Kapitel 1 bleibt unverändert erhalten.
    assert [c["title"] for c in result["chapters_written"]] == [
        "Grundlagen",
        "Architektur",
        "Fazit",
    ]
    assert result["chapters_written"][0]["content"] == "### Alt\n\nBereits geschrieben."
    # Die restlichen Kapitel wurden neu geschrieben (nicht leer).
    assert result["chapters_written"][1]["content"].strip() != ""
    assert result["chapters_written"][2]["content"].strip() != ""
    # Assembly enthält weiterhin alle drei Kapitel.
    assert "## 1. Grundlagen" in result["report"]
    assert "## 3. Fazit" in result["report"]


@pytest.mark.asyncio
async def test_deep_graph_resume_after_writer_crash(monkeypatch):
    """E2E-Resume (Kern des Geld-sparenden Verhaltens):

    1. Writer crasht bei Kapitel 2 (leere LLM-Antwort, Retries aus).
    2. Kapitel 1 wurde VOR der Exception als done-Event MIT Inhalt geliefert
       (=> Worker/Inline-Pfad hätte es bereits in der DB).
    3. Resume wie im Worker: aupdate_state(chapters_written) + astream(None).
    4. Danach: Outline + Recherche NICHT erneut gelaufen (keine doppelten
       Token-Kosten), Kapitel 1 bleibt unverändert, 2+3 werden geschrieben.
    """
    from langgraph.checkpoint.memory import InMemorySaver

    import app.agent.llm as llm_mod
    import app.agent.nodes.researcher as researcher_mod

    monkeypatch.setattr(llm_mod, "MAX_LLM_RETRIES", 1)
    monkeypatch.setattr(llm_mod, "RETRY_BASE_DELAY", 0.0)

    async def fake_search(query, max_results=5):
        return [
            {
                "title": f"Q {query[:10]}",
                "url": f"https://x/{abs(hash(query)) % 1000}",
                "snippet": "s",
            }
        ]

    monkeypatch.setattr(researcher_mod, "search_web", fake_search)

    class Chapter2CrashLLM(DeepFakeLLM):
        """Writer-Aufruf 2 liefert eine LEERE Antwort -> RuntimeError."""

        def __init__(self):
            self.outline_calls = 0
            self.research_calls = 0
            self.writer_calls = 0

        async def ainvoke(self, messages):
            if isinstance(messages, str):
                if "Outline-Planer" in messages:
                    self.outline_calls += 1
                elif "Fachautor" in messages:
                    self.writer_calls += 1
                    if self.writer_calls == 2:
                        return AIMessage(content="")  # Kapitel 2: leere Antwort
            else:
                self.research_calls += 1
            return await super().ainvoke(messages)

    llm = Chapter2CrashLLM()
    graph = build_deep_report_graph(checkpointer=InMemorySaver(), llm=llm)
    cfg = {"configurable": {"thread_id": "resume-test"}}
    initial = {
        "question": "FastAPI-Tiefenanalyse",
        "documents": [],
        "context_summary": "",
        "citation_style": "ieee",
    }

    # --- 1. Lauf: crasht bei Kapitel 2 ---
    events1: list[dict] = []
    with pytest.raises(RuntimeError, match="LLM-Call"):
        async for mode, payload in graph.astream(initial, cfg, stream_mode=["custom"]):
            if mode == "custom":
                events1.append(payload)

    done1 = [e for e in events1 if e.get("event") == "chapter" and e.get("status") == "done"]
    assert len(done1) == 1, "Kapitel 1 muss VOR dem Abbruch als done-Event ankommen"
    assert done1[0].get("content"), "done-Event muss den Kapitel-Inhalt tragen (Persistenz)"
    assert llm.outline_calls == 1
    research_after_first = llm.research_calls
    assert research_after_first > 0

    # --- "DB": gelieferte Kapitel wie Worker/Inline-Pfad persistieren ---
    saved_chapters = [{"title": d["title"], "content": d["content"]} for d in done1]

    # --- Resume wie im Worker: neuer Input MIT gespeicherten Kapiteln; die
    # Nodes sind idempotent (Outline-Skip, Research-Skip, Writer-Skip) ---
    resume_input = {
        "question": "FastAPI-Tiefenanalyse",
        "documents": [],
        "context_summary": "",
        "citation_style": "ieee",
        "chapters_written": saved_chapters,
    }
    final = await graph.ainvoke(resume_input, cfg)

    assert llm.outline_calls == 1, "Outline darf beim Resume NICHT neu geplant werden"
    assert llm.research_calls == research_after_first, (
        "Recherche darf beim Resume NICHT erneut laufen"
    )
    assert [c["title"] for c in final["chapters_written"]] == ["Grundlagen", "Architektur", "Fazit"]
    assert final["chapters_written"][0]["content"] == saved_chapters[0]["content"], (
        "Kapitel 1 muss unverändert aus dem Resume-Stamm übernommen werden"
    )
    assert "## 3. Fazit" in final["report"]
    assert "## 1. Grundlagen" in final["report"]
