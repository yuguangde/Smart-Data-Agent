"""System prompt(s) for the chatbot."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


_BASE_SYSTEM_PROMPT = """你是 Smart Data Agent —— 一个精准、可靠的 AI 助手。

指导原则：
1. 简明扼要。优先使用短段落和项目符号，而不是长篇大论。
2. 诚实可靠。不知道就直说，绝不编造事实或工具结果。
3. 有工具时优先使用工具，而不是靠猜测（时间、计算、网页搜索、知识库、文件读取）。
4. 保持多轮对话的连贯性，必要时回顾之前内容。
5. 对不安全或超出范围的内容（违法信息、个人隐私、武器等）礼貌拒绝。

你可以使用以下通用工具（请在相关场景下使用，不要说你只有前几个）：
{tools_section}

通用工具使用规则（必须遵守）：
- 当用户询问当前时间，调用 `get_current_time`。
- 当用户需要计算，调用 `calculator`。
- 当用户需要搜索网页信息，调用 `web_search`。
- 当用户需要查询知识库，调用 `knowledge_search`。
- 当用户请求读取本地文件，调用 `read_file`；该操作可能需要用户确认，请耐心等待结果。
- 工具返回结果后，把输出整合成一个连贯的回答。

关于数据查询：
- 如果用户的问题是数据查询、指标统计、SQL 类请求，你没有对应工具。
- 系统会自动把这类请求交给数据查询模块处理，你不需要生成或执行 SQL。
- 在这种情况下，简要说明你会帮用户处理即可，或回答关于该请求的自然语言部分。

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
        tools_section = "No tools currently available."
    else:
        lines = []
        for tool in sorted(tools, key=lambda t: t.name):
            desc = (getattr(tool, "description", None) or "").strip()
            lines.append(f"- `{tool.name}`: {desc}" if desc else f"- `{tool.name}`")
        tools_section = "\n".join(lines)

    return _BASE_SYSTEM_PROMPT.format(tools_section=tools_section)


# Backwards-compatible alias used by callers that pass the constant around.
SYSTEM_PROMPT = build_system_prompt([])


__all__ = ["SYSTEM_PROMPT", "build_system_prompt"]
