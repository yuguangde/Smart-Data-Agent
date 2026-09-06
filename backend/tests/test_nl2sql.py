"""Tests for the free-form nl2sql generator."""
from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.query.nl2sql import generate_nl_sql


class _FakeLLM:
    """Async LLM whose ainvoke always returns the provided content."""

    def __init__(self, content: str) -> None:
        self.content = content

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return AIMessage(content=self.content)


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    """Patch app.query.nl2sql.get_llm to return a fake LLM."""
    from app.query import nl2sql as nl2sql_mod

    def fake_get_llm(*args: Any, **kwargs: Any) -> _FakeLLM:
        return _FakeLLM(content)

    monkeypatch.setattr(nl2sql_mod, "get_llm", fake_get_llm)


@pytest.mark.anyio
async def test_generate_nl_sql_returns_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_sql = "SELECT * FROM db.users LIMIT 10"
    _patch_llm(monkeypatch, json.dumps({"sql": expected_sql}, ensure_ascii=False))

    result = await generate_nl_sql("最近登录的用户有哪些", schema_context="table: db.users")

    assert result["ok"] is True
    assert result["sql"] == expected_sql


@pytest.mark.anyio
async def test_generate_nl_sql_extracts_json_from_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_sql = "SELECT COUNT(*) FROM db.orders"
    payload = json.dumps({"sql": expected_sql}, ensure_ascii=False)
    _patch_llm(monkeypatch, f"```json\n{payload}\n```")

    result = await generate_nl_sql("有多少订单", schema_context="table: db.orders")

    assert result["ok"] is True
    assert result["sql"] == expected_sql


@pytest.mark.anyio
async def test_generate_nl_sql_errors_when_missing_sql_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(monkeypatch, json.dumps({"query": "SELECT 1"}, ensure_ascii=False))

    result = await generate_nl_sql("随便看看", schema_context="table: db.orders")

    assert result["ok"] is False
    assert "sql" in result["error"].lower()
    assert "raw" in result


@pytest.mark.anyio
async def test_generate_nl_sql_retries_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_sql = "SELECT 1"
    # Use a mutable index so the fake can switch responses across calls.
    responses = ["not json", json.dumps({"sql": expected_sql}, ensure_ascii=False)]
    from app.query import nl2sql as nl2sql_mod

    class _SwitchingFakeLLM:
        def __init__(self, responses: list[str]) -> None:
            self._responses = responses
            self._idx = 0

        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            content = self._responses[self._idx]
            self._idx += 1
            return AIMessage(content=content)

    def fake_get_llm(*args: Any, **kwargs: Any) -> _SwitchingFakeLLM:
        return _SwitchingFakeLLM(responses)

    monkeypatch.setattr(nl2sql_mod, "get_llm", fake_get_llm)

    result = await generate_nl_sql("测试重试", schema_context="table: db.orders")

    assert result["ok"] is True
    assert result["sql"] == expected_sql


__all__ = []
