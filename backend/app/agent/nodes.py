"""Graph nodes: agent (LLM) and the ToolNode runner."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.prebuilt import ToolNode

from app.agent.prompts import build_system_prompt
from app.agent.state import AgentState
from app.llm.factory import get_llm
from app.tools import get_all_tools

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


_MODULE_SIG: tuple | None = None
_GRAPH_CACHE: dict[str, object] = {}


def _build_model_with_tools() -> tuple["BaseChatModel", list["BaseTool"]]:
    tools = get_all_tools()
    chat = get_llm(with_tools=tools)
    return chat, tools


def current_tool_signature() -> tuple:
    """Return a hashable signature of the currently available tools.

    The signature is derived from the *names* of the registered tools (built-in
    + MCP). When the signature changes (for example after MCP has loaded new
    tools at startup), ``make_agent_node_cache_key`` flips and the cached
    compiled graph in ``graph.build_graph()`` is invalidated.
    """
    return tuple(sorted(t.name for t in get_all_tools()))


def make_agent_node():
    """Return a node that calls the LLM, injecting the system prompt on the first turn.

    Each invocation rebuilds ``chat`` only if env / settings changed; tools are
    unchanged across turns. We accept the small overhead rather than reaching
    for a module-level LLM singleton so test fixtures can override settings
    cleanly.
    """
    chat, tools = _build_model_with_tools()
    tool_node = ToolNode(tools)
    system_prompt = build_system_prompt(tools)

    async def agent_node(state: AgentState) -> AgentState:
        messages = state["messages"]
        # Inject system prompt exactly once, at the front.
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt), *messages]
            state["messages"] = messages

        # Use async invoke so LangGraph's astream_events() emits per-token
        # ``on_chat_model_stream`` events; the sync .invoke() blocks the event
        # loop and causes ``message → done → end`` with zero ``token`` frames.
        response: AIMessage = await chat.ainvoke(messages)
        return {
            "messages": [response],
            "iterations": state.get("iterations", 0) + 1,
        }

    return agent_node, tool_node


__all__ = ["make_agent_node", "current_tool_signature"]
