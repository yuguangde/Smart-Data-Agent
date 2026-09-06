"""Programmatic execution helpers for data-query tools.

These functions are meant to be called by graph nodes, not by the Agent LLM.
They avoid exposing SQL execution as a tool decision to the model.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import BaseTool

from app.tools import get_all_tools

logger = logging.getLogger(__name__)


def _get_tool(name: str) -> BaseTool | None:
    """Return the tool with ``name`` from the merged tool registry."""
    for tool in get_all_tools():
        if tool.name == name:
            return tool
    return None


async def execute_read_query(sql: str, db: str = "") -> dict[str, Any]:
    """Execute ``sql`` against the configured read-query tool (e.g. StarRocks).

    Raises:
        RuntimeError: if the read-query tool is not available.
        json.JSONDecodeError: if the tool response is not valid JSON.
    """
    tool = _get_tool("starrocks_read_query")
    if tool is None:
        raise RuntimeError("starrocks_read_query tool is not available")

    logger.debug("Executing SQL via %s", tool.name)
    raw = await tool.ainvoke({"query": sql, "db": db})

    if isinstance(raw, str):
        return json.loads(raw)
    return raw  # pragma: no cover - defensive


__all__ = ["_get_tool", "execute_read_query"]
