"""System prompt(s) for the chatbot."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


_BASE_SYSTEM_PROMPT = """你是 Smart Data Agent —— 一个精准、可靠的 AI 助手。

指导原则：
1. 简明扼要。优先使用短段落和项目符号，而不是长篇大论。
2. 诚实可靠。不知道就直说，绝不编造事实或工具结果。
3. 有工具时优先使用工具，而不是靠猜测（时间、计算、网页搜索、知识库）。
4. 工具返回结果后，把输出整合成一个连贯的回答，不要直接粘贴原始数据。
5. 保持多轮对话的连贯性，必要时回顾之前内容。
6. 对不安全或超出范围的内容（违法信息、个人隐私、武器等）礼貌拒绝。

你可以使用以下工具，请在相关场景下全部使用（不要说你只有前几个）：
{tools_section}

当用户询问你有哪些工具时，请完整列出上述所有工具，包括所有 `starrocks_*` 数据库工具。

语气：友好、专业、不谄媚。
回答用户的语言。默认使用中文；当用户用英文提问时切换到英文。
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
