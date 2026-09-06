"""Free-form natural language to SQL generator.

This module handles exploratory questions that are not strictly metric
analysis: it asks an LLM to generate raw SQL using the semantic layer as
schema context. No DSL validation is performed; correctness depends on the
LLM and the quality of the schema context.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm.factory import get_llm
from app.query._utils import _extract_json
from app.query.registry import SemanticRegistry

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
你是 Smart Data Agent 的 SQL 生成助手，专门处理自由探索类数据问题。

今天是：{today}

任务：
1. 根据下方 schema 上下文，把用户的自由探索问题转换成一条可以执行的 SQL 查询。
2. 查询必须基于语义层中已有的表（source）和维度字段，但你可以灵活地组合字段、筛选、分组、排序、LIMIT 等。
3. 输出必须 ONLY 是一个 JSON 对象，格式为：{{"sql": "你的 SQL 查询"}}。
4. 不要输出解释文字、markdown 代码块标记、或 SQL 以外的内容。
5. 目标方言：{dialect}。请使用该方言的函数和语法。

可用 schema 上下文：
{schema_context}

请只输出 JSON：{{"sql": "..."}}
"""


def _build_system_message(dialect: str, schema_context: str) -> SystemMessage:
    content = SYSTEM_PROMPT.format(
        today=date.today().isoformat(),
        dialect=dialect,
        schema_context=schema_context,
    )
    return SystemMessage(content=content)


def _build_user_prompt(question: str) -> HumanMessage:
    return HumanMessage(content=f"用户问题：{question}\n\n请生成对应的 SQL JSON：")


def _extract_sql_field(parsed: dict[str, Any]) -> str:
    """Return the SQL string from the parsed JSON or raise a clear error."""
    if not isinstance(parsed, dict):
        raise ValueError("输出必须是 JSON 对象")
    if "sql" not in parsed:
        raise ValueError("JSON 对象中缺少 'sql' 字段")
    sql = parsed["sql"]
    if not isinstance(sql, str):
        raise ValueError("'sql' 字段必须是字符串")
    sql = sql.strip()
    if not sql:
        raise ValueError("'sql' 字段不能为空")
    return sql


async def generate_nl_sql(
    question: str,
    *,
    dialect: str = "ansi",
    schema_context: str | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Generate a raw SQL string for an exploratory ``question``.

    The function makes up to ``max_retries + 1`` LLM calls. If the model does
    not return valid JSON with a ``sql`` field, the error text is fed back so
    it can self-correct.

    Returns:
        - ``{"ok": True, "sql": "..."}`` on success.
        - ``{"ok": False, "error": "...", "raw": "..."}`` on failure.
    """
    if schema_context is None:
        schema_context = SemanticRegistry.get().context_for_llm()

    messages = [
        _build_system_message(dialect, schema_context),
        _build_user_prompt(question),
    ]

    last_error: str | None = None
    last_raw: str | None = None

    llm = get_llm()

    for attempt in range(max_retries + 1):
        if last_error and last_raw:
            messages.append(
                HumanMessage(
                    content=(
                        f"上一次的输出不符合要求，请修复后重新生成：\n{last_error}\n"
                        f"原始输出：\n{last_raw}"
                    )
                )
            )

        response = await llm.ainvoke(messages)
        raw = response.content if hasattr(response, "content") else str(response)
        last_raw = raw
        cleaned = _extract_json(raw)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            last_error = f"JSON 解析失败: {exc}"
            logger.warning("NL2SQL attempt %d produced invalid JSON", attempt + 1)
            continue

        try:
            sql = _extract_sql_field(parsed)
            return {"ok": True, "sql": sql}
        except ValueError as exc:
            last_error = str(exc)
            logger.warning("NL2SQL attempt %d missing sql field: %s", attempt + 1, last_error)
            continue

    return {
        "ok": False,
        "error": f"无法生成合法 SQL（已重试 {max_retries} 次）。最后一次错误：\n{last_error}",
        "raw": last_raw,
    }


__all__ = ["generate_nl_sql"]
