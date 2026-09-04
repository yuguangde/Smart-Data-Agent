"""Human-in-the-loop review node for sensitive tool calls.

Currently guards ``read_file`` so the agent cannot read local files until the
user explicitly approves the call. The node uses LangGraph's ``interrupt``
primitive: when a read_file call is pending, the graph pauses and yields an
approval payload. Resuming with ``approved=True`` lets the tool run; otherwise
the read_file calls are stripped from the agent's tool batch while keeping any
non-sensitive calls intact.
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import interrupt

from app.agent.state import AgentState

SENSITIVE_TOOL_NAMES = {"read_file"}


def tool_review_node(state: AgentState) -> AgentState:
    """Pause the graph if any pending tool call is on the sensitive list."""
    messages = list(state.get("messages", []))
    if not messages:
        return state

    last_msg = messages[-1]
    if not isinstance(last_msg, AIMessage):
        return state

    tool_calls = list(getattr(last_msg, "tool_calls", None) or [])
    sensitive_calls = [
        tc for tc in tool_calls if tc.get("name") in SENSITIVE_TOOL_NAMES
    ]
    if not sensitive_calls:
        return state

    # Pause for user approval. On resume, ``interrupt`` returns the value
    # passed by ``Command(resume={"approved": True/False})``.
    response = interrupt(
        {
            "type": "tool_approval",
            "tool_calls": sensitive_calls,
            "message": (
                "Agent 请求读取本地文件。请确认是否允许执行以下工具调用？"
            ),
        }
    )

    approved = isinstance(response, dict) and response.get("approved") is True
    if approved:
        return state

    # Denied: strip the sensitive tool calls from the last assistant message and
    # append cancellation ToolMessages for them. Removing the tool_calls stops
    # the tools node from executing them; the ToolMessages record the denial and
    # prevent the LLM from immediately re-attempting the same call.
    allowed_calls = [
        tc for tc in tool_calls if tc.get("name") not in SENSITIVE_TOOL_NAMES
    ]
    messages[-1] = AIMessage(
        content=last_msg.content,
        id=last_msg.id,
        tool_calls=allowed_calls,
        additional_kwargs=last_msg.additional_kwargs,
    )

    cancellation_messages = [
        ToolMessage(
            content="用户拒绝了此工具调用。",
            tool_call_id=tc.get("id"),
            name=tc.get("name"),
        )
        for tc in sensitive_calls
        if tc.get("id")
    ]
    return {**state, "messages": list(messages) + cancellation_messages}


__all__ = ["tool_review_node", "SENSITIVE_TOOL_NAMES"]
