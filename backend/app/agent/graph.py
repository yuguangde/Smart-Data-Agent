"""LangGraph assembly: data-pipeline + general-agent + checkpointer + HITL.

The graph is split into two high-level paths:

1. Data questions: router -> generate_sql -> [sql_review if HITL] -> execute_sql -> synthesize.
2. General questions: agent -> marshal_tools -> [tool_review if sensitive] -> tools -> agent.

SQL execution and general tool execution are program-driven; the Agent LLM is
only responsible for reasoning and producing tool-call intentions.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import make_agent_node
from app.agent.query_nodes import (
    execute_sql_node,
    generate_sql_node,
    router_node,
    synthesize_node,
)
from app.agent.review import SENSITIVE_TOOL_NAMES, data_review_node, tool_review_node
from app.agent.state import AgentState
from app.config import get_settings
from app.memory.checkpointer import build_checkpointer

logger = logging.getLogger(__name__)


def _route_after_router(state: AgentState) -> str:
    """Route data questions into the SQL pipeline, others to the general agent."""
    return "generate_sql" if state.get("is_data_question") else "agent"


def _route_after_generate_sql(state: AgentState) -> str:
    """Insert the human-in-the-loop SQL review node when HITL is enabled."""
    settings = get_settings()
    if state.get("sql") and not state.get("query_error"):
        return "data_review" if settings.hitl else "execute_sql"
    # SQL generation failed or produced nothing: synthesize the explanation.
    return "synthesize"


def _route_after_data_review(state: AgentState) -> str:
    """After SQL review, either execute the SQL or synthesize the denial."""
    if state.get("execution_error") == "用户未授权执行该 SQL":
        return "synthesize"
    return "execute_sql"


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
    """Cap the general-agent loop with max_iterations, then hand off to marshal."""
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
    graph.add_node("router", router_node)
    graph.add_node("generate_sql", generate_sql_node)
    graph.add_node("data_review", data_review_node)
    graph.add_node("execute_sql", execute_sql_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("agent", agent_node)
    graph.add_node("marshal_tools", marshal_node)
    graph.add_node("review", tool_review_node)
    graph.add_node("tools", tool_node)

    # Data-pipeline path
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", _route_after_router)
    graph.add_conditional_edges("generate_sql", _route_after_generate_sql)
    graph.add_conditional_edges("data_review", _route_after_data_review)
    graph.add_edge("execute_sql", "synthesize")
    graph.add_edge("synthesize", END)

    # General-agent path (calculator, web_search, read_file, etc.)
    graph.add_conditional_edges("agent", _route_after_agent)
    graph.add_conditional_edges("marshal_tools", _route_after_marshal)
    graph.add_conditional_edges("review", _route_after_review)
    graph.add_edge("tools", "agent")

    settings = get_settings()
    checkpointer = build_checkpointer(settings)

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