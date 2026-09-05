"""Service layer wrapping the compiled LangGraph agent.

Owns invocation, streaming, and message extraction. The HTTP and WebSocket layers
stay thin and call into this module.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.types import Command

from app.agent.graph import get_compiled_graph
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


# ---------- Helpers ----------

# LangChain message roles -> frontend-friendly roles.
_ROLE_MAP = {
    "ai": "assistant",
    "human": "user",
}


def _normalise_role(role: str) -> str:
    """Map LangChain native roles to the roles the frontend expects."""
    return _ROLE_MAP.get(role, role)


def new_thread_id() -> str:
    """Return a fresh opaque thread identifier."""
    return uuid.uuid4().hex


def _plain_str(content: Any) -> str:
    """Coerce LangChain message content (str | list[dict]) to a flat string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                text = c.get("text") or c.get("content")
                if text:
                    parts.append(str(text))
            elif isinstance(c, str):
                parts.append(c)
        return "".join(parts)
    return str(content)


def _coerce_message(raw: Any) -> dict[str, Any]:
    """Normalise LangChain message objects and dicts into JSON-friendly shape."""
    if isinstance(raw, BaseMessage):
        msg_type = _normalise_role(raw.type)
        content = _plain_str(raw.content)
        out: dict[str, Any] = {"role": msg_type, "content": content}

        tc = getattr(raw, "tool_calls", None)
        if tc:
            serialised: list[dict[str, Any]] = []
            for call in tc:
                if isinstance(call, dict):
                    serialised.append(
                        {
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "input": call.get("args"),
                        }
                    )
                else:
                    serialised.append(
                        {
                            "id": getattr(call, "id", None),
                            "name": getattr(call, "name", None),
                            "input": getattr(call, "args", None),
                        }
                    )
            out["tool_calls"] = serialised

        tcid = getattr(raw, "tool_call_id", None)
        if tcid:
            out["tool_call_id"] = tcid
        return out

    # raw dict (e.g. streamed payload)
    msg_type = _normalise_role(raw.get("role") or raw.get("type", "user"))
    content = _plain_str(raw.get("content", ""))
    out = {"role": msg_type, "content": content}
    if raw.get("tool_calls"):
        out["tool_calls"] = raw["tool_calls"]
    if raw.get("tool_call_id"):
        out["tool_call_id"] = raw["tool_call_id"]
    return out


def _history_to_messages(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Return conversation history in the same shape the frontend sees while streaming.

    - Drops injected system prompts.
    - Drops standalone ToolMessages (their content lives inside the assistant tool_calls).
    - Merges consecutive assistant messages into one bubble so tool-calling
      turns look identical in history and streaming.
    """
    raw_msgs = values.get("messages", [])

    # First pass: coerce and collect assistant messages so we can attach tool outputs.
    out: list[dict[str, Any]] = []
    ai_messages: list[dict[str, Any]] = []

    def _flush_ai_group() -> None:
        if not ai_messages:
            return
        merged: dict[str, Any] = {
            "role": "assistant",
            "content": ai_messages[-1].get("content", ""),
        }
        merged_tool_calls: list[dict[str, Any]] = []
        for ai in ai_messages:
            for tc in ai.get("tool_calls") or []:
                if tc not in merged_tool_calls:
                    merged_tool_calls.append(tc)
        if merged_tool_calls:
            merged["tool_calls"] = merged_tool_calls
        out.append(merged)
        ai_messages.clear()

    for m in raw_msgs:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, ToolMessage):
            # Attach the tool output to the most recent assistant message that owns it.
            tool_call_id = getattr(m, "tool_call_id", None)
            output = _plain_str(m.content)
            for ai in reversed(ai_messages):
                for tc in ai.get("tool_calls") or []:
                    if tc.get("id") == tool_call_id:
                        tc["output"] = output
                        break
                else:
                    continue
                break
            continue

        cm = _coerce_message(m)
        if cm.get("role") in ("assistant", "ai"):
            ai_messages.append(cm)
        else:
            _flush_ai_group()
            out.append(cm)

    _flush_ai_group()
    return out


def _last_ai_message(values: dict[str, Any]) -> dict[str, Any] | None:
    for m in reversed(values.get("messages", [])):
        if isinstance(m, AIMessage):
            return _coerce_message(m)
    return None


def _aggregate_tool_calls(values: dict[str, Any]) -> list[dict[str, Any]]:
    """Pair each ToolMessage with the AIMessage tool_call record that triggered it."""
    calls: list[dict[str, Any]] = []
    last_ai: AIMessage | None = None
    for m in values.get("messages", []):
        if isinstance(m, AIMessage):
            last_ai = m
            continue
        if isinstance(m, ToolMessage) and last_ai is not None:
            tool_calls = list(getattr(last_ai, "tool_calls", []) or [])
            match = next(
                (
                    tc
                    for tc in tool_calls
                    if (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None))
                    == m.tool_call_id
                ),
                None,
            )
            if match is not None:
                if isinstance(match, dict):
                    name = match.get("name")
                    args = match.get("args", {})
                else:
                    name = getattr(match, "name", None)
                    args = getattr(match, "args", {})
                calls.append(
                    {
                        "id": m.tool_call_id,
                        "name": name,
                        "input": args,
                        "output": _plain_str(m.content),
                    }
                )
    return calls


def make_initial_state(
    thread_id: str,
    user_message: str,
    user_id: str,
    metadata: dict[str, Any],
) -> AgentState:
    """Build the input dict expected by LangGraph for a new turn."""
    return {
        "messages": [HumanMessage(content=user_message)],
        "user_id": user_id,
        "metadata": metadata,
        "iterations": 0,
    }


# ---------- Public API ----------

async def invoke(
    *,
    user_message: str,
    thread_id: str | None = None,
    user_id: str = "anonymous",
    metadata: dict[str, Any] | None = None,
    resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the agent once and return a JSON-friendly reply.

    If ``resume`` is provided, resumes a paused graph instead of starting a new
    turn (e.g. user approval for a sensitive tool call).
    """
    thread_id = thread_id or new_thread_id()
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}

    if resume is None:
        input_value = make_initial_state(thread_id, user_message, user_id, metadata or {})
    else:
        input_value = Command(resume=resume)

    result = await graph.ainvoke(input_value, config=config)

    # LangGraph surfaces dynamic interrupts under the ``__interrupt__`` key
    # rather than raising ``GraphInterrupt`` when using ``ainvoke``.
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        payload = payload if isinstance(payload, dict) else {}
        return {
            "thread_id": thread_id,
            "message": {"role": "assistant", "content": ""},
            "iterations": result.get("iterations", 0) if isinstance(result, dict) else 0,
            "tool_calls": _aggregate_tool_calls(result) if isinstance(result, dict) else [],
            "pending_approval": payload,
        }

    last = _last_ai_message(result) or {"role": "assistant", "content": ""}
    return {
        "thread_id": thread_id,
        "message": last,
        "iterations": result.get("iterations", 0) if isinstance(result, dict) else 0,
        "tool_calls": _aggregate_tool_calls(result) if isinstance(result, dict) else [],
    }


async def stream_events(
    *,
    user_message: str | None = None,
    thread_id: str | None = None,
    user_id: str = "anonymous",
    metadata: dict[str, Any] | None = None,
    resume: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE-friendly events describing the agent run.

    Schema: ``{"event": "message|token|tool_start|tool_end|done|error|tool_approval", "data": ...}``

    If ``resume`` is provided, the graph is resumed from a prior interrupt
    (e.g. user approval for a sensitive tool call) instead of starting a new turn.
    """
    graph = get_compiled_graph()
    thread_id = thread_id or new_thread_id()
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}

    if resume is None:
        input_value = make_initial_state(thread_id, user_message or "", user_id, metadata or {})
        yield {"event": "message", "data": {"thread_id": thread_id}}
    else:
        input_value = Command(resume=resume)

    try:
        async for ev in graph.astream_events(input_value, config=config, version="v2"):
            kind = ev.get("event")
            name = ev.get("name", "")
            data = ev.get("data", {}) or {}

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                text = _plain_str(getattr(chunk, "content", "") or "")
                if text:
                    yield {"event": "token", "data": text}
                tc_chunks = getattr(chunk, "tool_call_chunks", None)
                if tc_chunks:
                    for tc in tc_chunks:
                        if tc.get("name"):
                            yield {
                                "event": "tool_start",
                                "data": {
                                    "id": tc.get("id"),
                                    "name": tc.get("name"),
                                },
                            }

            elif kind == "on_tool_start":
                tname = data.get("name") or ev.get("name") or ""
                tool_input = data.get("input", {})
                yield {
                    "event": "tool_start",
                    "data": {
                        "name": tname,
                        "input": tool_input if isinstance(tool_input, dict) else {"value": tool_input},
                    },
                }

            elif kind == "on_tool_end":
                output = data.get("output", "")
                yield {
                    "event": "tool_end",
                    "data": {"output": _plain_str(output)},
                }

            elif kind == "on_chain_end" and name == "LangGraph":
                output = data.get("output", {})
                if hasattr(output, "values"):
                    output = output.values
                if isinstance(output, dict):
                    last = _last_ai_message(output)
                    if last:
                        yield {"event": "message", "data": last}
                    yield {
                        "event": "done",
                        "data": {
                            "thread_id": thread_id,
                            "iterations": output.get("iterations", 0),
                            "tool_calls": _aggregate_tool_calls(output),
                        },
                    }
                else:
                    yield {"event": "done", "data": {"thread_id": thread_id}}

    except Exception as exc:
        logger.exception("Streaming failed: %s", exc)
        yield {"event": "error", "data": str(exc)}
        return

    # After the event stream finishes, check whether the graph is paused on a
    # dynamic interrupt. ``ainvoke`` surfaces interrupts via ``__interrupt__``,
    # but ``astream_events`` does not raise an exception for them in v2.
    try:
        snapshot = await graph.aget_state(config=config)
    except Exception as exc:
        logger.warning("Failed to read graph state after stream: %s", exc)
        return

    interrupts = getattr(snapshot, "interrupts", None)
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        payload = payload if isinstance(payload, dict) else {}
        yield {"event": "tool_approval", "data": payload}


async def get_history(thread_id: str) -> list[dict[str, Any]]:
    """Return the conversation history attached to a ``thread_id``."""
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await graph.aget_state(config=config)
    except Exception as exc:
        logger.warning("get_history(%s) failed: %s", thread_id, exc)
        return []

    values = getattr(snapshot, "values", None)
    if isinstance(values, dict):
        return _history_to_messages(values)
    if isinstance(snapshot, dict):
        return _history_to_messages(snapshot)
    return []


__all__ = ["invoke", "stream_events", "get_history", "new_thread_id"]
