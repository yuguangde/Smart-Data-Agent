"""Background task that summarizes stale conversation context.

The summarizer runs hourly (configurable). For each thread it finds messages
outside the 2-hour retention window that have not yet been summarized,
passes them through the LLM, and stores the result. The stored summary is
later injected into the LLM context by ``_call_llm`` so long-running threads
do not lose important facts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import msgpack
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.agent.graph import get_compiled_graph
from app.agent.nodes import _CONTEXT_RETENTION_WINDOW, _msg_timestamp
from app.config import CheckpointerKind, get_settings
from app.llm.factory import get_llm
from app.memory.checkpointer import AsyncSqliteSaver, get_checkpointer
from app.memory.summary_store import SummaryStore, get_summary_store

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM_PROMPT = (
    "你是一名对话摘要助手。请总结以下历史对话，保留用户的核心问题、"
    "已确认的数据/事实、已执行的工具及其结果，以及任何需要后续回答继承的上下文。"
    "忽略闲聊和过程性套话。输出不超过 300 字。"
)

_SUMMARY_USER_TEMPLATE = """{existing_history}历史对话：
{transcript}

请输出纯文本摘要，不要解释。"""

_EXISTING_HISTORY_HEADER = "已有历史摘要：\n{summary}\n\n"


def _plain_str(content: Any) -> str:
    """Coerce LangChain message content to a flat string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content)


def _role_label(msg: BaseMessage) -> str:
    if isinstance(msg, HumanMessage):
        return "用户"
    if isinstance(msg, ToolMessage):
        return f"工具({msg.name or 'unknown'})"
    return "助手"


def _message_text(msg: BaseMessage) -> str:
    text = _plain_str(getattr(msg, "content", ""))
    if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
        calls = ", ".join(
            f"{tc.get('name') or 'tool'}(...)" for tc in msg.tool_calls
        )
        if text:
            text = f"{text} [调用: {calls}]"
        else:
            text = f"[调用: {calls}]"
    return text


def _build_transcript(messages: list[BaseMessage]) -> str:
    """Turn a list of messages into a plain-text transcript for summarization."""
    lines: list[str] = []
    for msg in messages:
        if getattr(msg, "additional_kwargs", None) and msg.additional_kwargs.get(
            "is_summary"
        ):
            continue
        lines.append(f"{_role_label(msg)}: {_message_text(msg)}")
    return "\n".join(lines)


async def _summarize_messages(
    messages: list[BaseMessage],
    existing_summary: str | None = None,
) -> str:
    """Call the LLM to produce a summary of the provided messages."""
    transcript = _build_transcript(messages)
    if not transcript.strip():
        return ""

    existing_history = (
        _EXISTING_HISTORY_HEADER.format(summary=existing_summary)
        if existing_summary
        else ""
    )
    user_content = _SUMMARY_USER_TEMPLATE.format(
        existing_history=existing_history,
        transcript=transcript,
    )

    chat = get_llm(with_tools=None)
    try:
        response = await asyncio.wait_for(
            chat.ainvoke(
                [
                    SystemMessage(content=_SUMMARY_SYSTEM_PROMPT),
                    HumanMessage(content=user_content),
                ],
                max_tokens=512,
            ),
            timeout=120.0,
        )
    except Exception as exc:
        logger.warning("Summarization LLM call failed: %s", exc)
        return ""

    return _plain_str(getattr(response, "content", "")).strip()


async def _list_thread_ids() -> list[str]:
    """Enumerate thread IDs from the SQLite checkpointer table."""
    checkpointer = get_checkpointer()
    if not isinstance(checkpointer, AsyncSqliteSaver):
        return []

    settings = get_settings()
    if settings.checkpointer != CheckpointerKind.SQLITE:
        return []

    path = str(settings.sqlite_path_resolved)
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT DISTINCT thread_id FROM checkpoints"
        ) as cursor:
            rows = await cursor.fetchall()
            return [str(row[0]) for row in rows]


async def _get_latest_checkpoint_ts(thread_id: str) -> datetime | None:
    """Return the write timestamp of the most recent checkpoint for a thread."""
    checkpointer = get_checkpointer()
    if not isinstance(checkpointer, AsyncSqliteSaver):
        return None

    settings = get_settings()
    if settings.checkpointer != CheckpointerKind.SQLITE:
        return None

    path = str(settings.sqlite_path_resolved)
    async with aiosqlite.connect(path) as db:
        async with db.execute(
            "SELECT checkpoint FROM checkpoints "
            "WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
            (thread_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                data = msgpack.unpackb(row[0])
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to unpack checkpoint for %s: %s", thread_id, exc)
                return None
            ts = data.get("ts")
            if not ts:
                return None
            return datetime.fromisoformat(ts)


async def _summarize_thread(
    thread_id: str,
    store: SummaryStore,
    cutoff: datetime,
) -> None:
    """Summarize window-outside messages for a single thread."""
    existing = await store.get_summary(thread_id)
    if existing:
        # Fast path: if no new checkpoint has been written since the last
        # summary, there is nothing new to summarize.
        latest_checkpoint_ts = await _get_latest_checkpoint_ts(thread_id)
        up_to = datetime.fromisoformat(existing["summarized_up_to"])
        if latest_checkpoint_ts is not None and latest_checkpoint_ts <= up_to:
            return

    graph = get_compiled_graph()
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    values = getattr(snapshot, "values", None)
    messages: list[BaseMessage] = list(values.get("messages", [])) if values else []

    existing_up_to_str = existing.get("summarized_up_to") if existing else None
    existing_up_to = (
        datetime.fromisoformat(existing_up_to_str) if existing_up_to_str else None
    )

    candidates: list[BaseMessage] = []
    latest_ts: datetime | None = None

    for msg in messages:
        if getattr(msg, "additional_kwargs", None) and msg.additional_kwargs.get(
            "is_summary"
        ):
            continue

        ts = _msg_timestamp(msg)
        if ts is not None:
            if ts >= cutoff:
                continue
            if existing_up_to is not None and ts <= existing_up_to:
                continue
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
        candidates.append(msg)

    if not candidates:
        return

    summary_text = await _summarize_messages(
        candidates,
        existing_summary=existing.get("summary") if existing else None,
    )
    if not summary_text:
        return

    summarized_up_to = (
        latest_ts.isoformat() if latest_ts else cutoff.isoformat()
    )
    settings = get_settings()
    model_label = settings.llm_provider.value
    await store.save_summary(
        thread_id=thread_id,
        summary=summary_text,
        summarized_up_to=summarized_up_to,
        model=model_label,
    )
    logger.info("Saved summary for thread %s (%d chars)", thread_id, len(summary_text))


async def run_summarize_all_once() -> None:
    """One-shot summarization pass over all known threads."""
    store = get_summary_store()
    if store is None:
        return

    cutoff = datetime.now(timezone.utc) - _CONTEXT_RETENTION_WINDOW
    thread_ids = await _list_thread_ids()
    if not thread_ids:
        return

    logger.info("Summarizer scanning %d thread(s)", len(thread_ids))
    for thread_id in thread_ids:
        try:
            await _summarize_thread(thread_id, store, cutoff)
        except Exception as exc:
            logger.warning("Failed to summarize thread %s: %s", thread_id, exc)


async def _hourly_summarizer_task(
    store: SummaryStore,
    interval_seconds: int,
) -> None:
    """Infinite loop that runs the summarizer once per interval."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await run_summarize_all_once()
        except Exception as exc:
            logger.warning("Hourly summarizer run failed: %s", exc)


async def start_summarizer_task() -> asyncio.Task[None] | None:
    """Start the background hourly summarizer if persistence is enabled."""
    store = get_summary_store()
    if store is None:
        return None

    settings = get_settings()
    interval = max(settings.summary_interval_seconds, 60)
    logger.info("Starting hourly summarizer (interval=%ds)", interval)
    return asyncio.create_task(_hourly_summarizer_task(store, interval))


__all__ = [
    "run_summarize_all_once",
    "start_summarizer_task",
]
