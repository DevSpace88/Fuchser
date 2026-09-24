"""
agent/llm.py — LLM binding for all agent nodes (DeepSeek or Z.ai GLM)
==========================================================================

Two providers, ONE factory — selected via LLM_PROVIDER in the .env:

    deepseek (default)
        ChatDeepSeek -> https://api.deepseek.com (langchain-deepseek)

    glm  (Z.ai "GLM Coding Plan", international API — NOT bigmodel.cn)
        ChatOpenAI with base_url = https://api.z.ai/api/coding/paas/v4
        (OpenAI-compatible protocol, see docs.z.ai/devpack/quick-start).
        Current coding-plan models: glm-5.3, glm-5.3-flash.

Fallback cascade: if the selected provider has no key, the other one is
tried; if that has no key either, we return None -> echo mode (the app
starts anyway and stays testable).

Note: under the Z.ai terms of use, the coding plan is really meant for
"officially supported tools" — fine for our learning project, but not
as a blueprint for production.

WHY a factory instead of direct instantiation in the nodes?
    * ONE place for model/key/temperature (configuration from .env).
    * Tests inject fake LLMs — that's why build_*_graph() also takes
      the LLM as a parameter.
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
    Returns the chat model — or None if no provider key is available.

    temperature=0.2: research should be factually accurate, with just a hint of variation.
    streaming=True:   token streaming for the live output in the frontend.
    """
    provider = settings.llm_provider

    if provider == "glm" and settings.glm_api_key:
        return _build_glm(temperature, streaming)
    if provider == "glm" and settings.deepseek_api_key:
        # GLM selected, but no GLM key -> DeepSeek as fallback.
        return _build_deepseek(temperature, streaming)

    if provider == "deepseek" and settings.deepseek_api_key:
        return _build_deepseek(temperature, streaming)
    if settings.glm_api_key:
        # DeepSeek selected, but only a GLM key present -> use GLM.
        return _build_glm(temperature, streaming)

    return None  # echo mode


def _build_deepseek(temperature: float, streaming: bool) -> ChatDeepSeek:
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        temperature=temperature,
        streaming=streaming,
        # IMPORTANT: the default is 4096 — long reports get cut off
        # mid-sentence ("it just stops"). 8k is deepseek-chat's maximum.
        max_tokens=8192,
    )


def _build_glm(temperature: float, streaming: bool) -> ChatOpenAI:
    # ChatOpenAI is the universal client for EVERY OpenAI-compatible API —
    # here, Z.ai's coding-plan endpoint (rest of the world).
    return ChatOpenAI(
        model=settings.glm_model,
        api_key=settings.glm_api_key,
        base_url=settings.glm_base_url,
        temperature=temperature,
        streaming=streaming,
        max_tokens=8192,  # same truncation trap as with DeepSeek
    )


# ----------------------------------------------------------------------------
# Retry wrapper: repeat LLM calls with exponential backoff.
# DeepSeek/GLM rate-limit under parallel load — instead of empty responses
# (empty content) or dropped connections, it then fails cleanly.
# ----------------------------------------------------------------------------

MAX_LLM_RETRIES = 4
RETRY_BASE_DELAY = 10.0  # seconds, doubled per attempt (10/20/40s)
# DeepSeek rate limits are aggressive — short delays are not enough.


async def ainvoke_with_retry(llm, prompt, context: str = ""):
    """
    Calls llm.ainvoke and retries on:
      * empty content (rate-limit response)
      * exceptions (connection errors)
    With exponential backoff (2s, 4s, 8s).
    """
    last_error = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            response = await llm.ainvoke(prompt)
            content = str(response.content).strip()
            if content:
                return response
            # Empty response = often a rate limit -> wait and try again
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
        except Exception as e:  # noqa: BLE001 — network/rate limit
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
