"""LLM factory: returns a LangChain ChatModel bound with tools."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel

from app.config import LLMProvider, Settings, get_settings

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


def get_llm(settings: Settings | None = None, *, with_tools: list["BaseTool"] | None = None) -> BaseChatModel:
    """Build a chat model based on settings, optionally bound with tools."""
    settings = settings or get_settings()
    chat = _build_chat_model(settings)
    if with_tools:
        # `bind_tools` is supported across LangChain chat models.
        chat = chat.bind_tools(with_tools)
    return chat


def _build_chat_model(settings: Settings) -> BaseChatModel:
    provider = settings.llm_provider
    logger.info("Building LLM: provider=%s", provider)

    if provider == LLMProvider.OPENAI:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when llm_provider=openai")
        from langchain_openai import ChatOpenAI

        kwargs: dict = {
            "model": settings.openai_model,
            "api_key": settings.openai_api_key,
            "temperature": settings.openai_temperature,
            "max_tokens": settings.max_tokens,
            "streaming": True,
        }
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        return ChatOpenAI(**kwargs)

    if provider == LLMProvider.ANTHROPIC:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when llm_provider=anthropic")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            temperature=settings.openai_temperature,
            max_tokens=settings.max_tokens,
            streaming=True,
        )

    if provider == LLMProvider.DEEPSEEK:
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when llm_provider=deepseek")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=settings.openai_temperature,
            max_tokens=settings.max_tokens,
            streaming=True,
        )

    if provider == LLMProvider.QWEN:
        if not settings.qwen_api_key:
            raise ValueError("QWEN_API_KEY is required when llm_provider=qwen")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.qwen_model,
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            temperature=settings.openai_temperature,
            max_tokens=settings.max_tokens,
            streaming=True,
        )

    raise ValueError(f"Unsupported llm_provider: {provider}")


__all__ = ["get_llm"]
