"""Graph nodes: agent (LLM) and the ToolNode runner."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.prebuilt import ToolNode

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import AgentState
from app.llm.factory import get_llm
from app.tools import get_all_tools

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


def _build_model_with_tools() -> tuple["BaseChatModel", list["BaseTool"]]:
    tools = get_all_tools()
    chat = get_llm(with_tools=tools)
    return chat, tools


def make_agent_node():
    """Return a node that calls the LLM, injecting the system prompt on the first turn.

    Each invocation rebuilds `chat` only if env / settings changed; tools are unchanged
    across turns. We accept the small overhead rather than reaching for a module-level LLM
    singleton so test fixtures can override settings cleanly.
    """
    chat, _ = _build_model_with_tools()
    tool_node = ToolNode(get_all_tools())

    def agent_node(state: AgentState) -> AgentState:
        messages = state["messages"]
        # Inject system prompt exactly once, at the front.
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
            state["messages"] = messages
        response: AIMessage = chat.invoke(messages)
        return {
            "messages": [response],
            "iterations": state.get("iterations", 0) + 1,
        }

    return agent_node, tool_node


__all__ = ["make_agent_node"]
