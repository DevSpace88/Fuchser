"""
agent/language.py — language lector for deep-report chapters
==========================================================

Why does this module exist?
    DeepSeek/GLM occasionally show two weaknesses:
      1. Code-switching: Chinese (more rarely Cyrillic/Japanese)
         characters appear in the middle of German text.
      2. "Pseudo-Anglicisms": invented Germanized English words that
         do not exist in that form.

Cost strategy (the user pays per token!):
    1. FOREIGN SCRIPT => regex check, 0 tokens. On a hit: force a correction.
    2. Otherwise mini-check => LLM answers only YES/NO (~1 output token).
    3. Only on YES (or a hit from 1.) => correction call that returns
       the reworked chapter.

The correction is content-faithful: [n] citations, headings and facts
are kept exactly as they are — only the language level is touched.
"""

import logging
import re

from app.agent.events import emit
from app.agent.llm import ainvoke_with_retry
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

# Foreign writing systems that have no business in a German report:
# CJK (Chinese), Kana (Japanese), Hangul (Korean), Cyrillic.
# Deliberately WITHOUT Greek/Latin (mathematics, units of measure).
FOREIGN_SCRIPT_RE = re.compile(
    "["
    "\u4e00-\u9fff"  # CJK Unified Ideographs (Chinese)
    "\u3400-\u4dbf"  # CJK Extension A
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
    "\u3040-\u30ff"  # Hiragana + Katakana (Japanese)
    "\uac00-\ud7af"  # Hangul Syllables (Korean)
    "\u0400-\u04ff"  # Cyrillic
    "]"
)


def has_foreign_script(text: str) -> bool:
    """True if the text contains characters from foreign writing systems (0 tokens)."""
    return bool(FOREIGN_SCRIPT_RE.search(text or ""))


CHECK_PROMPT = """Du prüfst deutsche Fachtexte auf Sprachmängel.

Enthält der folgende Text eines davon?
- Fremde Schriftzeichen (chinesisch, kyrillisch, japanisch, ...)
- Erfundene Schein-Anglizismen oder unnatürliche Denglisch-Wortschöpfungen (z. B.
  "Compensation-Modelle", "Job-Search-Strategien", "Skillset-Matching", wo saubere
  deutsche Begriffe wie "Vergütungsmodelle", "Bewerbungsstrategien", "Kompetenzprofil"
  existieren)
- Grobe Grammatik-/Orthographiefehler

Antworte AUSSCHLIESSLICH mit YES oder NO.

Text:
{text}"""

POLISH_PROMPT = """Du bist ein Lektor für deutsche Fachtexte. Korrigiere den folgenden
Text AUSSCHLIESSLICH auf Sprachebene:

- Übersetze fremdsprachige Passagen (z. B. chinesische oder kyrillische Zeichen) ins Deutsche
- Ersetze Schein-Anglizismen und unnatürliches Denglisch durch korrektes, idiomatisch
  sauberes Fachdeutsch (z. B. "Vergütungsmodelle" statt "Compensation-Modelle",
  "Gehaltsvergleich" / "Gehaltsbenchmarks" statt "Compensation-Benchmarks")
- Korrigiere Grammatik- und Orthographiefehler

STRENG VERBOTEN: Inhalt, Fakten, Zahlen, die ###-Überschriften oder die
[n]-Zitate zu verändern. Zitate [n] müssen EXAKT erhalten bleiben. Kürze
oder erweitere den Text nicht.

Gib AUSSCHLIESSLICH den korrigierten Text aus, ohne jeden Kommentar.

Text:
{text}"""


async def polish_german(model, text: str, context: str = "") -> tuple[str, list[dict]]:
    """
    Lectorates a German text (chapter). Returns (text, usage).

    Without a model (echo mode) or with empty text: unchanged, 0 usage.
    If the correction fails (empty response), the ORIGINAL text is
    returned — a language lector must never destroy a chapter.
    """
    if model is None or not text or not text.strip():
        return text, []

    forced = has_foreign_script(text)
    needs_fix = forced
    usage: list[dict] = []

    if not forced:
        # Mini-check: YES/NO only — costs ~1 output token. Deliberately
        # WITHOUT a retry loop (ainvoke_with_retry): an empty/failed check
        # answer simply counts as "NO" instead of burning 70s of backoff.
        try:
            check = await model.ainvoke(CHECK_PROMPT.format(text=text))
            usage.append(extract_usage(check))
            needs_fix = "yes" in str(check.content).strip().lower()
        except Exception:  # noqa: BLE001 — the check is best-effort
            logger.warning("Sprach-Check fehlgeschlagen (%s) — übersprungen", context)
        if not needs_fix:
            return text, usage

    emit(
        "tool",
        tool="language_polish",
        query=f"Sprachkorrektur{' (fremde Schrift)' if forced else ''} — {context[:40]}",
        agent="lector",
    )
    try:
        fixed = await ainvoke_with_retry(
            model, POLISH_PROMPT.format(text=text), context=f"{context} [Lektor]"
        )
    except Exception:  # noqa: BLE001 — the lector must not kill the run
        logger.warning("Sprachkorrektur fehlgeschlagen (%s) — Original bleibt", context)
        return text, usage
    usage.append(extract_usage(fixed))

    corrected = str(fixed.content).strip()
    # Safety net: discard empty or cryptically broken corrections.
    if not corrected or len(corrected) < len(text) * 0.5:
        logger.warning(
            "Sprachkorrektur lieferte verdächtig kurzes Ergebnis (%s) — Original bleibt", context
        )
        return text, usage
    return corrected, usage
