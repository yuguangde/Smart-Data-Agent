"""System prompt for the review agent.

The review agent re-executes the analysis using a different LLM and produces a
second report. It must not look at the primary agent's conclusion; it must
independently query the data and write its own Markdown report.
"""
from __future__ import annotations

from app.agent.prompts import build_system_prompt

_REVIEW_ROLE_APPENDIX = """

---

你现在是“复核分析师”。你的任务是基于同样的用户问题，独立地重新做一次数据分析，并输出一份新的分析报告。

重要约束：
1. 不要参考任何其他 Agent 已经给出的结论，你必须自己重新查询数据。
2. 你看到的上下文里只包含：用户问题、之前轮次的工具调用及其结果。把这些当作原始资料使用。
3. 如果重新查询后得到的结果与上下文中已有的结果不一致，请如实报告差异，不要强行调和。
4. 输出格式与正常 Data Agent 一致：先给出核心结论，再用 Markdown 表格/列表展示关键数据，最后给出简短分析。
5. 如果数据为空或无法验证，明确说明“无法复核”或“该条件下没有数据”。
""".strip()


def build_review_system_prompt(tools: list) -> str:
    """Return the review-agent system prompt.

    Reuses the primary agent's tool/prompt scaffolding, then appends the
    review-specific role instructions.
    """
    base = build_system_prompt(tools)
    return f"{base}\n\n{_REVIEW_ROLE_APPENDIX}"
