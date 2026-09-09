"""Tests for the conversation summarization system."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.nodes import _call_llm
from app.config import CheckpointerKind
from app.memory.summary_store import SummaryStore, init_summary_store
from app.tasks.summarizer import _build_transcript


@pytest.fixture
async def temp_store() -> SummaryStore:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = SummaryStore(path)
    await store.ensure_schema()
    try:
        yield store
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_summary_store_crud(temp_store: SummaryStore) -> None:
    row = await temp_store.get_summary("thread-x")
    assert row is None

    now = datetime.now(timezone.utc).isoformat()
    await temp_store.save_summary("thread-x", "early summary", now, "deepseek")
    row = await temp_store.get_summary("thread-x")
    assert row is not None
    assert row["summary"] == "early summary"
    assert row["summarized_up_to"] == now
    assert row["model"] == "deepseek"

    later = datetime.now(timezone.utc).isoformat()
    await temp_store.save_summary("thread-x", "updated summary", later)
    row = await temp_store.get_summary("thread-x")
    assert row["summary"] == "updated summary"
    assert row["summarized_up_to"] == later


@pytest.mark.asyncio
async def test_summary_store_update_preserves_thread(temp_store: SummaryStore) -> None:
    await temp_store.save_summary("t1", "s1", "2026-01-01T00:00:00+00:00")
    await temp_store.save_summary("t2", "s2", "2026-01-02T00:00:00+00:00")

    assert (await temp_store.get_summary("t1"))["summary"] == "s1"
    assert (await temp_store.get_summary("t2"))["summary"] == "s2"


def test_build_transcript_skips_summary_messages() -> None:
    messages: list[SystemMessage | HumanMessage] = [
        HumanMessage(content="hi"),
        SystemMessage(
            content="this is a summary",
            additional_kwargs={"is_summary": True},
        ),
        HumanMessage(content="bye"),
    ]
    transcript = _build_transcript(messages)
    assert "this is a summary" not in transcript
    assert "用户: hi" in transcript
    assert "用户: bye" in transcript


def test_build_transcript_with_tool_calls() -> None:
    ai = AIMessage(
        content="",
        tool_calls=[{"id": "1", "name": "execute_sql", "args": {}}],
    )
    tool = SystemMessage(content="not used")  # type: ignore[arg-type]
    assert _build_transcript([ai]) == "助手: [调用: execute_sql(...)]"


@pytest.mark.asyncio
async def test_call_llm_injects_summary(monkeypatch) -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    store = SummaryStore(path)
    await store.ensure_schema()
    await store.save_summary(
        "t1",
        "用户询问 8 月智能服务量，月末异常。",
        datetime.now(timezone.utc).isoformat(),
    )

    monkeypatch.setattr(
        "app.agent.nodes.get_summary_store", lambda: store
    )

    chat = AsyncMock()
    chat.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))

    state = {
        "messages": [
            HumanMessage(
                content="继续分析",
                additional_kwargs={"timestamp": datetime.now(timezone.utc).isoformat()},
            ),
        ]
    }
    response = await _call_llm(state, chat, "sys prompt", thread_id="t1")
    assert response.content == "ok"
    assert chat.ainvoke.called

    llm_messages = chat.ainvoke.call_args[0][0]
    assert len(llm_messages) == 3
    assert isinstance(llm_messages[0], SystemMessage)
    assert llm_messages[0].content == "sys prompt"
    assert isinstance(llm_messages[1], SystemMessage)
    assert llm_messages[1].additional_kwargs.get("is_summary") is True
    assert "用户询问 8 月智能服务量" in llm_messages[1].content
    assert isinstance(llm_messages[2], HumanMessage)

    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_init_summary_store_disabled_in_memory_mode(monkeypatch) -> None:
    class FakeSettings:
        checkpointer = CheckpointerKind.MEMORY

    monkeypatch.setattr("app.memory.summary_store.get_settings", lambda: FakeSettings())
    monkeypatch.setattr("app.memory.summary_store._store", None)
    store = await init_summary_store()
    assert store is None
