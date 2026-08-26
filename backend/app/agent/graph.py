"""LangGraph assembly: agents + tools + checkpointer + HITL."""
from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from app.agent.nodes import make_agent_node
from app.agent.state import AgentState
from app.config import CheckpointerKind, get_settings
from app.memory.checkpointer import build_checkpointer

logger = logging.getLogger(__name__)


def _should_continue(state: AgentState) -> str:
    """After the agent runs we either call tools or end. Cap iterations to avoid loops."""
    settings = get_settings()
    if state.get("iterations", 0) >= settings.max_iterations:
        logger.warning("max_iterations=%d hit; stopping loop", settings.max_iterations)
        return END
    return tools_condition(state)  # 'tools' or END


def build_graph():
    """Compile and return the LangGraph agent."""
    agent_node, tool_node = make_agent_node()

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    settings = get_settings()
    checkpointer = build_checkpointer(settings)

    interrupt_before: list[str] = ["agent"] if settings.hitl else []

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before or None,
    )
    logger.info(
        "Compiled graph: checkpointer=%s hitl=%s",
        settings.checkpointer,
        settings.hitl,
    )
    return compiled


@lru_cache
def get_compiled_graph():
    return build_graph()


__all__ = ["build_graph", "get_compiled_graph"]
