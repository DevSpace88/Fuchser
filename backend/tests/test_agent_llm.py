"""
tests/test_agent_llm.py — Provider selection in agent/llm.py (DeepSeek vs. GLM)
===============================================================================

The factory get_llm() decides, based on the settings, which model object
comes back (or None -> echo mode). We patch the settings attributes
directly (no .env fiddling) and check the cascade:

    glm selected + GLM key    -> ChatOpenAI with the z.ai coding endpoint
    glm selected, DS key only -> ChatDeepSeek (fallback)
    deepseek + DS key         -> ChatDeepSeek
    deepseek, GLM key only    -> ChatOpenAI (fallback)
    no key at all             -> None (echo mode)
"""

import pytest
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from app.agent import llm
from app.core.config import get_settings


@pytest.fixture
def settings_patch(monkeypatch):
    """Sets provider keys in a controlled way and cleans up afterwards."""
    s = get_settings()
    stash = {attr: getattr(s, attr) for attr in ("llm_provider", "deepseek_api_key", "glm_api_key")}

    def set_state(provider: str, deepseek: str, glm: str) -> None:
        object.__setattr__(s, "llm_provider", provider)
        object.__setattr__(s, "deepseek_api_key", deepseek)
        object.__setattr__(s, "glm_api_key", glm)

    yield set_state
    for attr, value in stash.items():
        object.__setattr__(s, attr, value)


@pytest.mark.asyncio
async def test_glm_provider_uses_zai_endpoint(settings_patch):
    settings_patch("glm", deepseek="", glm="fake-glm-key")
    model = llm.get_llm()
    assert isinstance(model, ChatOpenAI)
    # base_url must be the international coding-plan endpoint
    # (NOT bigmodel.cn!) — see docs.z.ai/devpack/quick-start.
    assert "api.z.ai/api/coding" in model.openai_api_base


@pytest.mark.asyncio
async def test_glm_provider_falls_back_to_deepseek(settings_patch):
    settings_patch("glm", deepseek="fake-ds-key", glm="")
    assert isinstance(llm.get_llm(), ChatDeepSeek)


@pytest.mark.asyncio
async def test_deepseek_provider_default(settings_patch):
    settings_patch("deepseek", deepseek="fake-ds-key", glm="fake-glm-key")
    assert isinstance(llm.get_llm(), ChatDeepSeek)


@pytest.mark.asyncio
async def test_deepseek_without_key_falls_back_to_glm(settings_patch):
    settings_patch("deepseek", deepseek="", glm="fake-glm-key")
    assert isinstance(llm.get_llm(), ChatOpenAI)


@pytest.mark.asyncio
async def test_no_keys_returns_none_echo_mode(settings_patch):
    settings_patch("deepseek", deepseek="", glm="")
    assert llm.get_llm() is None
