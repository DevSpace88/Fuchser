"""
tests/test_language.py — Language editor (agent/language.py)
============================================================

Covered:
  * Foreign script characters (Chinese/Cyrillic) are detected via regex.
  * Clean German text + NO answer => NO correction call (token-savvy).
  * YES answer => the correction call runs and replaces the text.
  * Chinese characters => correction is FORCED (without a pre-check).
  * Empty/crashed correction => the original is kept (safety net).
"""

import pytest
from langchain_core.messages import AIMessage

from app.agent.language import has_foreign_script, polish_german


def test_has_foreign_script():
    assert has_foreign_script("Der Markt wächst auf 12 Prozent 数据")
    assert has_foreign_script("Цена составляет")
    assert has_foreign_script("日本語のテキスト")
    assert not has_foreign_script("Der Brandenburger Wohnungsmarkt, äöüß — 12 %")
    assert not has_foreign_script("")


class LektorFakeLLM:
    """Answers the language check (YES/NO) and editor prompts in a testable way."""

    def __init__(self, check_answer: str = "NO", polish_answer: str | None = None):
        self.check_answer = check_answer
        self.polish_answer = polish_answer
        self.calls: list[str] = []

    async def ainvoke(self, prompt):
        text = str(prompt)
        self.calls.append(text)
        if "YES oder NO" in text:
            return AIMessage(content=self.check_answer)
        if "Lektor" in text:
            return AIMessage(content=self.polish_answer if self.polish_answer is not None else "")
        raise AssertionError(f"Unerwarteter Prompt: {text[:60]}")


@pytest.mark.asyncio
async def test_polish_clean_text_no_extra_call():
    """Clean chapter + NO => only the mini check, no expensive correction."""
    llm = LektorFakeLLM(check_answer="NO")
    text = "Der Wohnungsmarkt in Brandenburg wächst stabil seit 2020 [1]."
    out, usage = await polish_german(llm, text, "Kapitel 1: Test")
    assert out == text
    assert len(usage) == 1  # only the check
    assert any("YES oder NO" in c for c in llm.calls)
    assert not any("Lektor für deutsche Fachtexte" in c for c in llm.calls)


@pytest.mark.asyncio
async def test_polish_yes_triggers_correction():
    llm = LektorFakeLLM(check_answer="YES", polish_answer="Korrigierter deutscher Text [1].")
    out, usage = await polish_german(llm, "Der Wohnungsmarkt downgeregelt [1].", "Kapitel 1")
    assert out == "Korrigierter deutscher Text [1]."
    assert len(usage) == 2  # check + correction
    assert any("Lektor" in c for c in llm.calls)


@pytest.mark.asyncio
async def test_polish_chinese_forces_correction_without_check():
    """Chinese characters => correction immediately (no YES/NO pre-check)."""
    llm = LektorFakeLLM(polish_answer="Übersetzter deutscher Text [1].")
    out, usage = await polish_german(llm, "半分のデータ zeigt den Trend [1].", "Kapitel 2")
    assert out == "Übersetzter deutscher Text [1]."
    assert not any("YES oder NO" in c for c in llm.calls)  # check skipped
    assert any("Lektor" in c for c in llm.calls)


@pytest.mark.asyncio
async def test_polish_empty_correction_falls_back_to_original(monkeypatch):
    # Disable retries so the test does not end up in backoff sleeps.
    import app.agent.llm as llm_mod

    monkeypatch.setattr(llm_mod, "MAX_LLM_RETRIES", 1)
    monkeypatch.setattr(llm_mod, "RETRY_BASE_DELAY", 0.0)

    llm = LektorFakeLLM(polish_answer="")
    original = "半分 der Text bleibt [1]." + " filler " * 50
    out, _usage = await polish_german(llm, original, "Kapitel 3")
    assert out == original  # the original stays — the editor never destroys a chapter


@pytest.mark.asyncio
async def test_polish_without_model_is_noop():
    out, usage = await polish_german(None, "半分 [1].", "Kapitel")
    assert usage == []
    assert out == "半分 [1]."
