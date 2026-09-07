"""Tool registry: merges built-in tools with MCP-provided tools.

Built-in tools are imported eagerly and always available. MCP tools (see
``app.tools.mcp_loader``) are loaded lazily during FastAPI lifespan
startup and exposed through ``get_mcp_tools()``.

If MCP is disabled or initialisation fails, only the built-in tools are
returned so the agent keeps working in a degraded mode.
"""
from __future__ import annotations

import logging

from langchain_core.tools import BaseTool

from app.tools.calculator import calculator
from app.tools.data_query import execute_metric_dsl, execute_sql
from app.tools.datetime_tool import get_current_time
from app.tools.file_reader import read_file
from app.tools.knowledge_search import knowledge_search
from app.tools.web_search import web_search

logger = logging.getLogger(__name__)

# Names of tools that should only be called by code, not exposed to the LLM.
_PROGRAM_ONLY_TOOL_NAMES: set[str] = {"generate_sql", "starrocks_read_query"}

# Built-in tools that ship with the application.
BUILTIN_TOOLS: list[BaseTool] = [
    get_current_time,
    calculator,
    web_search,
    knowledge_search,
    read_file,
    execute_metric_dsl,
    execute_sql,
    # Register additional built-in tools here.
]


def get_builtin_tools() -> list[BaseTool]:
    """Return the static built-in tool list."""
    return list(BUILTIN_TOOLS)


def get_mcp_tools() -> list[BaseTool]:
    """Return currently registered MCP tools (cache-backed).

    Imports ``app.tools.mcp_loader`` lazily to avoid static module-level
    coupling (tests that exercise only built-in tools do not need the MCP
    client to be configured).
    """
    try:
        from app.tools.mcp_loader import list_mcp_tools as _mcp_list

        return _mcp_list()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("MCP tools unavailable: %s", exc)
        return []


def get_all_tools() -> list[BaseTool]:
    """Return the merged list of built-in + MCP tools."""
    mcp = get_mcp_tools()
    if not mcp:
        return list(BUILTIN_TOOLS)
    return [*BUILTIN_TOOLS, *mcp]


def get_llm_tools() -> list[BaseTool]:
    """Return only tools that should be visible to the Agent LLM.

    SQL generation and execution are program-driven, so ``generate_sql`` and
    ``starrocks_read_query`` are filtered out.
    """
    return [
        t for t in get_all_tools() if t.name not in _PROGRAM_ONLY_TOOL_NAMES
    ]


def get_program_only_tool_names() -> set[str]:
    """Return the set of tool names reserved for program-driven execution."""
    return set(_PROGRAM_ONLY_TOOL_NAMES)


# Backwards-compatible alias used by callers that expect a bare list.
ALL_TOOLS = BUILTIN_TOOLS


__all__ = [
    "ALL_TOOLS",
    "BUILTIN_TOOLS",
    "get_all_tools",
    "get_builtin_tools",
    "get_mcp_tools",
    "get_llm_tools",
    "get_current_time",
    "calculator",
    "web_search",
    "knowledge_search",
    "read_file",
]
