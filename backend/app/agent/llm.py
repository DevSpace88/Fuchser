"""
agent/llm.py — LLM-Bindung für alle Agent-Nodes (DeepSeek oder Z.ai GLM)
==========================================================================

Zwei Provider, EINE Factory — ausgewählt über LLM_PROVIDER in der .env:

    deepseek (Standard)
        ChatDeepSeek -> https://api.deepseek.com (langchain-deepseek)

    glm  (Z.ai "GLM Coding Plan", internationale API — NICHT bigmodel.cn)
        ChatOpenAI mit base_url = https://api.z.ai/api/coding/paas/v4
        (OpenAI-kompatibles Protokoll, siehe docs.z.ai/devpack/quick-start).
        Aktuelle Coding-Plan-Modelle: glm-5.3, glm-5.3-flash.

Fallback-Kaskade: Ist der gewählte Provider ohne Key, wird der jeweils
andere probiert; hat der auch keinen Key, liefern wir None -> Echo-Modus
(App startet und bleibt testbar).

Hinweis: Laut Z.ai-Nutzungsbedingungen ist der Coding-Plan eigentlich für
"officially supported tools" gedacht — für unser Lernprojekt ok, aber
nicht als Blaupause für Produktion.

WARUM eine Factory statt direkter Instanziierung in den Nodes?
    * EIN Ort für Modell/Key/Temperature (Konfiguration aus .env).
    * Tests injizieren Fake-LLMs — deshalb nimmt build_*_graph() das
      LLM auch als Parameter.
"""

import asyncio
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_llm(
    temperature: float = 0.2,
    streaming: bool = True,
) -> BaseChatModel | None:
    """
    Liefert das Chat-Modell — oder None, wenn kein Provider-Key da ist.

    temperature=0.2: Recherche soll faktentreu sein, nur ein Hauch Variation.
    streaming=True:   Token-Streaming für die Live-Ausgabe im Frontend.
    """
    provider = settings.llm_provider

    if provider == "glm" and settings.glm_api_key:
        return _build_glm(temperature, streaming)
    if provider == "glm" and settings.deepseek_api_key:
        # GLM gewählt, aber kein GLM-Key -> DeepSeek als Fallback.
        return _build_deepseek(temperature, streaming)

    if provider == "deepseek" and settings.deepseek_api_key:
        return _build_deepseek(temperature, streaming)
    if settings.glm_api_key:
        # DeepSeek gewählt, aber nur GLM-Key vorhanden -> GLM nehmen.
        return _build_glm(temperature, streaming)

    return None  # Echo-Modus


def _build_deepseek(temperature: float, streaming: bool) -> ChatDeepSeek:
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        temperature=temperature,
        streaming=streaming,
        # WICHTIG: Default ist 4096 — lange Reports werden mitten im Satz
        # abgeschnitten ("hört einfach auf"). 8k ist deepseek-chats Maximum.
        max_tokens=8192,
    )


def _build_glm(temperature: float, streaming: bool) -> ChatOpenAI:
    # ChatOpenAI ist der universelle Client für JEDE OpenAI-kompatible API —
    # hier der Coding-Plan-Endpoint von Z.ai (Rest der Welt).
    return ChatOpenAI(
        model=settings.glm_model,
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
        temperature=temperature,
        streaming=streaming,
        max_tokens=8192,  # gleiche Abschneide-Falle wie bei DeepSeek
    )


# ----------------------------------------------------------------------------
# Retry-Wrapper: LLM-Calls mit exponentiellem Backoff wiederholen.
# DeepSeek/GLM raten-limiten bei paralleler Last — statt leerer Antworten
# (leerer content) oder Verbindungsabbrüchen crasht es dann sauber durch.
# ----------------------------------------------------------------------------

MAX_LLM_RETRIES = 4
RETRY_BASE_DELAY = 10.0  # Sekunden, verdoppelt pro Versuch (10/20/40s)
# DeepSeek-Rate-Limits sind aggressiv — kurze Delays reichen nicht.


async def ainvoke_with_retry(llm, prompt, context: str = ""):
    """
    Ruft llm.ainvoke auf und wiederholt bei:
      * leerem content (Rate-Limit-Antwort)
      * Exceptions (Verbindungsfehler)
    Mit exponentiellem Backoff (2s, 4s, 8s).
    """
    last_error = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            response = await llm.ainvoke(prompt)
            content = str(response.content).strip()
            if content:
                return response
            # Leere Antwort = oft Rate-Limit -> warten und erneut versuchen
            logger.warning(
                "Leere LLM-Antwort (Versuch %d/%d)%s — warte %.0fs …",
                attempt, MAX_LLM_RETRIES,
                f" [{context}]" if context else "",
                RETRY_BASE_DELAY * (2 ** (attempt - 1)),
            )
            from app.agent.events import emit as _emit
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            _emit(
                "tool", tool="retry",
                query=f"Rate-Limit — warte {delay:.0f}s ({attempt}/{MAX_LLM_RETRIES})",
                agent="system",
            )
            last_error = ValueError("Leere LLM-Antwort")
        except Exception as e:  # noqa: BLE001 — Netzwerk/Rate-Limit
            logger.warning(
                "LLM-Call fehlgeschlagen (Versuch %d/%d)%s: %s",
                attempt, MAX_LLM_RETRIES,
                f" [{context}]" if context else "",
                e,
            )
            last_error = e
        if attempt < MAX_LLM_RETRIES:
            await asyncio.sleep(RETRY_BASE_DELAY * (2 ** (attempt - 1)))
    raise RuntimeError(
        f"LLM-Call nach {MAX_LLM_RETRIES} Versuchen fehlgeschlagen {context}: {last_error}"
    )
