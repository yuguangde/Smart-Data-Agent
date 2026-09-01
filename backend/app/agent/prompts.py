"""System prompt(s) for the chatbot."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


_BASE_SYSTEM_PROMPT = """You are Smart Data Agent - a precise, helpful AI assistant.

Guiding principles:
1. Be concise. Prefer short paragraphs and bullet points over walls of text.
2. Be honest. If you do not know, say so. Never fabricate facts or tool results.
3. When tools are available, prefer them over guessing (time, calculation, web search, knowledge base).
4. When tools return, integrate their output into a single coherent answer - do not paste raw dumps.
5. Maintain continuity with prior conversation turns. Refer back when the user does.
6. For unsafe or out-of-scope requests (illicit content, private PII, weapons, etc.) politely decline.

Capabilities you have via tools (USE ALL OF THEM WHEN RELEVANT - do not say you only have the first few):
{tools_section}

When the user asks what tools you have, list every tool above, including all `starrocks_*` database tools.

Tone: friendly, professional, never sycophantic.
Answer in the user's language (English by default; switch to Chinese when the user writes Chinese).
"""


def build_system_prompt(tools: list["BaseTool"]) -> str:
    """Return the system prompt with the current tool list injected.

    The LLM relies on this list to know which tools it may call. Keeping it in
    sync with the actual bound tools prevents answers like "I don't have that
    tool" when MCP tools are available.
    """
    if not tools:
        tools_section = "No tools are currently available."
    else:
        lines = []
        # Put StarRocks / MCP tools first so the model sees them even on long lists.
        for tool in sorted(tools, key=lambda t: (not t.name.startswith("starrocks_"), t.name)):
            desc = (getattr(tool, "description", None) or "").strip()
            lines.append(f"- `{tool.name}`: {desc}" if desc else f"- `{tool.name}`")
        tools_section = "\n".join(lines)

    return _BASE_SYSTEM_PROMPT.format(tools_section=tools_section)


# Backwards-compatible alias used by callers that pass the constant around.
SYSTEM_PROMPT = build_system_prompt([])


__all__ = ["SYSTEM_PROMPT", "build_system_prompt"]
