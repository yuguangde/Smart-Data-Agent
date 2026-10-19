"""Independent review-agent graph.

The review agent uses a *different* LLM from the primary agent, re-executes the
same data-analysis workflow, and produces a second report. It runs in its own
thread so it never writes into the primary thread's checkpoints.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    _call_llm,
    _serializing_awrap_tool_call,
    _utc_now_iso,
    build_tool_node,
    make_marshal_node,
)
from app.agent.review import tool_review_node
from app.agent.review_prompts import build_review_system_prompt
from app.agent.state import AgentState
from app.config import get_settings
from app.llm.factory import get_review_llm
from app.memory.checkpointer import get_checkpointer
from app.tools import get_llm_tools
from langchain_core.messages import ToolMessage

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


def _route_after_review_agent(state: AgentState) -> str:
    """Cap the review loop with review_max_iterations, then hand off to marshal."""
    settings = get_settings()
    if state.get("iterations", 0) >= settings.review_max_iterations:
        logger.warning(
            "review_max_iterations=%d hit; stopping review loop",
            settings.review_max_iterations,
        )
        return END
    return "marshal_tools"


def _route_after_review_marshal(state: AgentState) -> str:
    """Decide whether pending tool calls need HIL review or can run directly."""
    if state.get("pending_tool_calls"):
        return "review"
    if _has_pending_tool_calls(state):
        return "tools"
    return END


def _route_after_review_node(state: AgentState) -> str:
    """After HIL review, either execute the restored tool calls or return to agent."""
    if _has_pending_tool_calls(state):
        return "tools"
    return "agent"


def _build_review_model_with_tools():
    """Return the review LLM bound with tools and the tool list."""
    tools = get_llm_tools()
    chat = get_review_llm().bind_tools(tools)
    return chat, tools


def _make_review_agent_node():
    """Build the review-agent LLM node and its underlying tool runner/marshal."""
    settings = get_settings()
    chat, tools = _build_review_model_with_tools()
    # Tool-free chat for the final iteration to avoid truncated tool_calls.
    chat_final = get_review_llm()
    raw_tool_node = build_tool_node(tools)
    marshal_node = make_marshal_node()
    system_prompt = build_review_system_prompt(tools)

    async def agent_node(
        state: AgentState,
        config,
    ) -> AgentState:
        thread_id = config.get("configurable", {}).get("thread_id") if config else None
        iterations = state.get("iterations", 0)
        is_last_turn = iterations + 1 >= settings.review_max_iterations
        current_chat = chat_final if is_last_turn else chat
        response = await _call_llm(
            state,
            current_chat,
            system_prompt,
            thread_id=thread_id,
            final_turn=is_last_turn,
        )
        response.additional_kwargs = {
            **(response.additional_kwargs or {}),
            "timestamp": _utc_now_iso(),
        }
        return {
            "messages": [response],
            "iterations": iterations + 1,
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


def build_review_graph() -> StateGraph:
    """Compile and return the review LangGraph.

    The graph mirrors the primary agent's topology but uses the review LLM and
    review system prompt. It is intended to run under a separate thread_id.
    """
    agent_node, tool_node, marshal_node = _make_review_agent_node()

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("marshal_tools", marshal_node)
    graph.add_node("review", tool_review_node)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route_after_review_agent)
    graph.add_conditional_edges("marshal_tools", _route_after_review_marshal)
    graph.add_conditional_edges("review", _route_after_review_node)
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=get_checkpointer())


@lru_cache
def get_compiled_review_graph():
    """Cached compiled review graph."""
    return build_review_graph()


__all__ = ["build_review_graph", "get_compiled_review_graph"]
