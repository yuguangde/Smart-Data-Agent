"""Tests for the programmatic SQL executor helper."""
from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.tools import tool

from app.agent.executor import execute_read_query


@tool
def starrocks_read_query(query: str, db: str = "") -> str:
    """Fake StarRocks read tool for unit tests."""
    return json.dumps({"columns": ["c1"], "rows": [["v1"]]}, ensure_ascii=False)


@pytest.mark.anyio
async def test_execute_read_query_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agent.executor.get_all_tools",
        lambda: [starrocks_read_query],
    )

    result = await execute_read_query("SELECT 1", db="")

    assert result["columns"] == ["c1"]
    assert result["rows"] == [["v1"]]


@pytest.mark.anyio
async def test_execute_read_query_tool_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.agent.executor.get_all_tools", lambda: [])

    with pytest.raises(RuntimeError, match="starrocks_read_query tool is not available"):
        await execute_read_query("SELECT 1", db="")


@pytest.mark.anyio
async def test_execute_read_query_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    @tool
    def starrocks_read_query(query: str, db: str = "") -> str:
        """Fake read query that returns invalid JSON."""
        return "not json"

    monkeypatch.setattr("app.agent.executor.get_all_tools", lambda: [starrocks_read_query])

    with pytest.raises(json.JSONDecodeError):
        await execute_read_query("SELECT 1", db="")


__all__ = []
