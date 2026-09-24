"""
agent/language.py — Sprach-Lektor für Deep-Report-Kapitel
==========================================================

Warum existiert dieses Modul?
    DeepSeek/GLM zeigen gelegentlich zwei Schwächen:
      1. Code-Switching: mitten im deutschen Text erscheinen chinesische
         (seltener kyrillische/japanische) Zeichen.
      2. "Schein-Anglizismen": erfundene eingedeutschte Englisch-Wörter,
         die so nicht existieren.

Kosten-Strategie (der User zahlt pro Token!):
    1. FREMDE SCHRIFT => Regex-Check, 0 Token. Bei Fund: Korrektur erzwingen.
    2. Sonst Mini-Check => LLM antwortet nur YES/NO (~1 Output-Token).
    3. Nur bei YES (oder Fund aus 1.) => Korrektur-Call, der das Kapitel
       überarbeitet zurückgibt.

Die Korrektur ist inhalts-treu: [n]-Zitate, Überschriften und Fakten
bleiben exakt erhalten — nur die Sprachebene wird angefasst.
"""

import logging
import re

from app.agent.events import emit
from app.agent.llm import ainvoke_with_retry
from app.agent.usage import extract_usage

logger = logging.getLogger(__name__)

# Fremde Schriftsysteme, die in einem deutschen Bericht nichts verloren
# haben: CJK (Chinesisch), Kana (Japanisch), Hangul (Koreanisch),
# Kyrillisch. Bewusst OHNE Griechisch/Latein (Mathematik, Maßeinheiten).
FOREIGN_SCRIPT_RE = re.compile(
    "["
    "\u4e00-\u9fff"  # CJK Unified Ideographs (Chinesisch)
    "\u3400-\u4dbf"  # CJK Extension A
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
    "\u3040-\u30ff"  # Hiragana + Katakana (Japanisch)
    "\uac00-\ud7af"  # Hangul Syllables (Koreanisch)
    "\u0400-\u04ff"  # Kyrillisch
    "]"
)


def has_foreign_script(text: str) -> bool:
    """True, wenn der Text Zeichen aus fremden Schriftsystemen enthält (0 Token)."""
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
    Lektoriert einen deutschen Text (Kapitel). Liefert (text, usage) zurück.

    Ohne Modell (Echo-Modus) oder bei leerem Text: unverändert, 0 Usage.
    Schlägt die Korrektur fehl (leere Antwort), wird der ORIGINAL-Text
    zurückgegeben — Sprachlektor darf niemals ein Kapitel zerstören.
    """
    if model is None or not text or not text.strip():
        return text, []

    forced = has_foreign_script(text)
    needs_fix = forced
    usage: list[dict] = []

    if not forced:
        # Mini-Check: nur YES/NO — kostet ~1 Output-Token. Bewusst OHNE
        # Retry-Schleife (ainvoke_with_retry): ein leere/fehlerhafte Check-
        # Antwort gilt einfach als "NO" statt 70s Backoff zu verbraten.
        try:
            check = await model.ainvoke(CHECK_PROMPT.format(text=text))
            usage.append(extract_usage(check))
            needs_fix = "yes" in str(check.content).strip().lower()
        except Exception:  # noqa: BLE001 — Check ist best-effort
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
    except Exception:  # noqa: BLE001 — Lektor darf den Lauf nicht killen
        logger.warning("Sprachkorrektur fehlgeschlagen (%s) — Original bleibt", context)
        return text, usage
    usage.append(extract_usage(fixed))

    corrected = str(fixed.content).strip()
    # Sicherheitsnetz: leere oder kiptisch kaputte Korrektur verwerfen.
    if not corrected or len(corrected) < len(text) * 0.5:
        logger.warning(
            "Sprachkorrektur lieferte verdächtig kurzes Ergebnis (%s) — Original bleibt", context
        )
        return text, usage
    return corrected, usage
