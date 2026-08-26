"""Pytest configuration & shared fixtures."""
from __future__ import annotations

import os
from typing import AsyncIterator

import pytest

# Set deterministic test env before app imports.
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-a-real-key")
os.environ.setdefault("CHECKPOINTER", "memory")
os.environ.setdefault("HITL", "false")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """Ensure each test starts with a fresh settings read."""
    from app.config import get_settings

    get_settings.cache_clear()