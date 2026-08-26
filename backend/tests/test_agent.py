"""Smoke tests for the LangGraph agent.

These tests do NOT require a live LLM API key. They validate the wiring only.
"""
from __future__ import annotations

import pytest

from app.agent.graph import get_compiled_graph
from app.agent.state import AgentState
from app.config import LLMProvider, get_settings
from app.tools import get_all_tools


def test_settings_load() -> None:
    s = get_settings()
    assert s.llm_provider in [p.value for p in LLMProvider]
    assert s.checkpointer.value in {"memory", "sqlite"}
    assert s.max_iterations >= 1


def test_tools_registration() -> None:
    tools = get_all_tools()
    names = {t.name for t in tools}
    assert "get_current_time" in names
    assert "calculator" in names


def test_graph_compiles() -> None:
    """The graph should compile without contacting the LLM."""
    g = get_compiled_graph()
    assert g is not None


def test_state_shape() -> None:
    s: AgentState = {
        "messages": [],
        "user_id": "test",
        "metadata": {},
        "iterations": 0,
    }
    assert s["user_id"] == "test"
    assert s["iterations"] == 0


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    """Smoke test the health endpoint via TestClient (no LLM call)."""
    from fastapi.testclient import TestClient  # type: ignore[import-untyped]

    from app.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "llm_provider" in body