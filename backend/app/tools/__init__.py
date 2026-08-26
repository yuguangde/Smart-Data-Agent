"""Tool registry: import all tool instances in one place."""
from __future__ import annotations

from langchain_core.tools import BaseTool

from app.tools.calculator import calculator
from app.tools.datetime_tool import get_current_time
from app.tools.knowledge_search import knowledge_search
from app.tools.web_search import web_search

ALL_TOOLS: list[BaseTool] = [
    get_current_time,
    calculator,
    web_search,
    knowledge_search,
    # Register additional tools here.
]


def get_all_tools() -> list[BaseTool]:
    """Return the mutable list of all tools."""
    return ALL_TOOLS


__all__ = ["ALL_TOOLS", "get_all_tools", "get_current_time", "calculator", "web_search", "knowledge_search"]
