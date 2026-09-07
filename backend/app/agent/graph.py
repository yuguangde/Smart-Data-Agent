"""LangGraph assembly: agent decides, tools execute, checkpointer persists.

The graph is now a single agent loop:

    START -> agent -> marshal_tools -> [tool_review if sensitive] -> tools -> agent -> END

Data questions are handled by the agent via two dedicated tools:

- ``nl2dsl`` for metric-style analysis (preferred).
- ``nl2sql`` for free-form exploration (fallback).

Both tools generate and execute SQL internally; the Agent LLM only reasons about
which tool to call and how to answer based on the returned results.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import make_agent_node
from app.agent.review import SENSITIVE_TOOL_NAMES, tool_review_node
from app.agent.state import AgentState
from app.config import get_settings
from app.memory.checkpointer import get_checkpointer

logger = logging.getLogger(__name__)


def _has_pending_tool_calls(state: AgentState) -> bool:
    """Return True if the last message contains non-empty tool calls."""
    messages = state.get("messages", [])
    if not messages:
        return False
    last_msg = messages[-1]
    return isinstance(last_msg, AIMessage) and bool(
        getattr(last_msg, "tool_calls", None)
    )


def _route_after_agent(state: AgentState) -> str:
    """Cap the agent loop with max_iterations, then hand off to marshal."""
    settings = get_settings()
    if state.get("iterations", 0) >= settings.max_iterations:
        logger.warning("max_iterations=%d hit; stopping agent loop", settings.max_iterations)
        return END
    return "marshal_tools"


def _route_after_marshal(state: AgentState) -> str:
    """Decide whether pending tool calls need HIL review, can run directly, or are done."""
    if state.get("pending_tool_calls"):
        return "review"
    if _has_pending_tool_calls(state):
        return "tools"
    return END


def _route_after_review(state: AgentState) -> str:
    """After HIL review, either execute the restored tool calls or return to agent."""
    if _has_pending_tool_calls(state):
        return "tools"
    return "agent"


def build_graph():
    """Compile and return the LangGraph agent."""
    agent_node, tool_node, marshal_node = make_agent_node()

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("marshal_tools", marshal_node)
    graph.add_node("review", tool_review_node)
    graph.add_node("tools", tool_node)

    # Agent loop
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route_after_agent)
    graph.add_conditional_edges("marshal_tools", _route_after_marshal)
    graph.add_conditional_edges("review", _route_after_review)
    graph.add_edge("tools", "agent")

    settings = get_settings()
    checkpointer = get_checkpointer()

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info(
        "Compiled graph: checkpointer=%s hitl=%s sensitive_tools=%s",
        settings.checkpointer,
        settings.hitl,
        sorted(SENSITIVE_TOOL_NAMES),
    )
    return compiled


@lru_cache
def get_compiled_graph():
    return build_graph()


__all__ = ["build_graph", "get_compiled_graph"]
