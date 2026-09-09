"""Graph nodes: agent (LLM), tool marshal, and the ToolNode runner."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.prebuilt import ToolNode

from app.agent.prompts import build_system_prompt
from app.agent.review import SENSITIVE_TOOL_NAMES
from app.agent.state import AgentState
from app.config import get_settings
from app.llm.factory import get_llm
from app.tools import get_llm_tools, get_program_only_tool_names

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

# Serialize tool invocations.  LangGraph's ToolNode executes tool_calls in
# parallel by default; combined with MCP tools that share a single session,
# this has led to empty tool outputs and state corruption.  This lock forces
# one tool call at a time.
_TOOL_CALL_SERIAL_LOCK = asyncio.Lock()

# Retention window for messages passed to the LLM. Messages older than this
# are dropped so long-running threads do not overflow the model context.
_CONTEXT_RETENTION_WINDOW = timedelta(hours=2)


def _utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _msg_timestamp(msg: BaseMessage) -> datetime | None:
    """Extract a message timestamp from its additional_kwargs, if present."""
    raw = getattr(msg, "additional_kwargs", None) or {}
    ts = raw.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:  # pragma: no cover - defensive
        return None


def _filter_recent_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Drop messages outside the retention window and ensure the first kept
    message is a human message.

    Tool messages whose parent AI/tool_call request was dropped are also
    removed to keep the message sequence valid for the chat model.
    """
    if not messages:
        return messages

    now = datetime.now(timezone.utc)
    cutoff = now - _CONTEXT_RETENTION_WINDOW

    retained: list[BaseMessage] = []
    for msg in messages:
        ts = _msg_timestamp(msg)
        # Missing timestamp means "unknown / pre-policy message": treat as old.
        if ts is not None and ts < cutoff:
            continue
        retained.append(msg)

    # Drop tool messages whose matching assistant tool_call is no longer kept.
    cleaned: list[BaseMessage] = []
    for msg in retained:
        if isinstance(msg, ToolMessage):
            parent_present = any(
                isinstance(prev, AIMessage)
                and prev.tool_calls is not None
                and any(
                    tc.get("id") == msg.tool_call_id for tc in prev.tool_calls
                )
                for prev in cleaned
            )
            if not parent_present:
                continue
        cleaned.append(msg)

    # Ensure the first retained message is from the user.
    first_human = next(
        (i for i, m in enumerate(cleaned) if isinstance(m, HumanMessage)),
        None,
    )
    if first_human is None:
        return []
    return cleaned[first_human:]


async def _serializing_awrap_tool_call(request, execute):
    """Wrapper that executes each tool call under a global async lock.

    Also enforces ``mcp_call_timeout_seconds`` so a hanging StarRocks/MCP call
    does not leave the graph stuck at a pending tool_call forever.
    """
    settings = get_settings()
    timeout = max(settings.mcp_call_timeout_seconds, 1.0)

    async with _TOOL_CALL_SERIAL_LOCK:
        try:
            return await asyncio.wait_for(execute(request), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Tool call timed out after %.1fs", timeout)
            return f"工具调用超时（>{timeout}s），未获得响应"


def _build_model_with_tools() -> tuple["BaseChatModel", list["BaseTool"]]:
    tools = get_llm_tools()
    chat = get_llm(with_tools=tools)
    return chat, tools


def current_tool_signature() -> tuple:
    """Return a hashable signature of the currently available tools.

    The signature is derived from the *names* of the registered tools (built-in
    + MCP). When the signature changes (for example after MCP has loaded new
    tools at startup), ``make_agent_node_cache_key`` flips and the cached
    compiled graph in ``graph.build_graph()`` is invalidated.
    """
    return tuple(sorted(t.name for t in get_llm_tools()))


async def _call_llm(
    state: AgentState,
    chat: "BaseChatModel",
    system_prompt: str,
) -> AIMessage:
    """Run the LLM with the system prompt injected on the first turn."""
    # Keep only messages inside the retention window and start with a user turn.
    messages = _filter_recent_messages(list(state.get("messages", [])))

    # Inject system prompt exactly once, at the front.
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_prompt), *messages]

    # Use async invoke so LangGraph's astream_events() emits per-token
    # ``on_chat_model_stream`` events; the sync .invoke() blocks the event
    # loop and causes ``message → done → end`` with zero ``token`` frames.
    return await chat.ainvoke(messages)


def make_agent_node():
    """Return the agent LLM node, the ToolNode runner, and the tool marshal node.

    Each invocation rebuilds ``chat`` only if env / settings changed; tools are
    unchanged across turns. We accept the small overhead rather than reaching
    for a module-level LLM singleton so test fixtures can override settings
    cleanly.
    """
    chat, tools = _build_model_with_tools()
    # Serialize tool execution to avoid empty outputs caused by parallel MCP
    # invocations under a shared session.
    raw_tool_node = ToolNode(
        tools, awrap_tool_call=_serializing_awrap_tool_call
    )
    marshal_node = make_marshal_node()
    system_prompt = build_system_prompt(tools)

    async def agent_node(state: AgentState) -> AgentState:
        response: AIMessage = await _call_llm(state, chat, system_prompt)
        # Timestamp the assistant message for retention policy.
        response.additional_kwargs = {
            **(response.additional_kwargs or {}),
            "timestamp": _utc_now_iso(),
        }
        return {
            "messages": [response],
            "iterations": state.get("iterations", 0) + 1,
        }

    async def tool_node(state: AgentState) -> AgentState:
        """Run tools and stamp each ToolMessage with a timestamp."""
        result = await raw_tool_node.ainvoke(state)
        for msg in result.get("messages", []):
            if isinstance(msg, ToolMessage):
                msg.additional_kwargs = {
                    **(msg.additional_kwargs or {}),
                    "timestamp": _utc_now_iso(),
                }
        return result

    return agent_node, tool_node, marshal_node


def make_marshal_node():
    """Return a node that validates and routes LLM-produced tool calls.

    The LLM only reasons about which tools to call; this node enforces the
    boundary between reasoning and execution:

    - Filters out program-only tools (e.g. ``starrocks_read_query``) so the
      agent can never bypass the data pipeline or the SQL executor.
    - Detects sensitive tools (e.g. ``read_file``) and pauses the graph by
      storing the pending calls in ``pending_tool_calls`` for HIL review.
    - Leaves non-sensitive calls in the last assistant message so ToolNode can
      execute them directly.
    """
    program_only = get_program_only_tool_names()
    sensitive = SENSITIVE_TOOL_NAMES

    def _rewrite_last_ai(
        messages: list[BaseMessage],
        tool_calls: list[dict[str, Any]] | None,
    ) -> list[BaseMessage]:
        """Replace the last AIMessage with a copy whose tool_calls are updated."""
        if not messages:
            return messages
        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage):
            return messages
        messages[-1] = AIMessage(
            content=last_msg.content,
            id=last_msg.id,
            tool_calls=tool_calls or [],
            additional_kwargs=last_msg.additional_kwargs,
        )
        return messages

    async def marshal_node(state: AgentState) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        if not messages:
            return {"pending_tool_calls": None}

        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage):
            return {"pending_tool_calls": None}

        tool_calls = list(getattr(last_msg, "tool_calls", None) or [])
        if not tool_calls:
            return {"pending_tool_calls": None}

        # Defensive filter: the LLM should never see program-only tools, but
        # guard against misconfiguration that exposes them.
        allowed_calls = [
            tc for tc in tool_calls if tc.get("name") not in program_only
        ]
        filtered = [tc for tc in tool_calls if tc.get("name") in program_only]
        if filtered:
            logger.warning(
                "Filtered program-only tool calls the LLM should not have produced: %s",
                [tc.get("name") for tc in filtered],
            )

        if not allowed_calls:
            return {
                "messages": _rewrite_last_ai(messages, []),
                "pending_tool_calls": None,
            }

        sensitive_calls = [tc for tc in allowed_calls if tc.get("name") in sensitive]
        if sensitive_calls:
            # Erase tool_calls from the assistant message so ToolNode will not
            # execute them until the review node restores them.
            return {
                "messages": _rewrite_last_ai(messages, []),
                "pending_tool_calls": allowed_calls,
            }

        # Non-sensitive calls: proceed straight to ToolNode.
        return {
            "messages": _rewrite_last_ai(messages, allowed_calls),
            "pending_tool_calls": None,
        }

    return marshal_node


__all__ = ["make_agent_node", "current_tool_signature", "make_marshal_node"]
