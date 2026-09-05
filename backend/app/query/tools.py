"""LangChain tool wrappers for the query DSL.

These tools allow the agent to convert a natural-language metric question into
a controlled DSL JSON without querying the database.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.query.generator import generate_metric_query


@tool
async def generate_dsl_json(question: str) -> dict[str, Any]:
    """根据语义层配置，把自然语言指标分析问题转换为 MetricQuery DSL JSON。

    只是生成并校验 DSL，不执行 SQL 查询数据库。返回的 JSON 中：
    - ok=true 时包含 ``query`` 字段
    - ok=false 时包含 ``error`` 字段

    Args:
        question: 用户的自然语言指标分析问题，例如"最近7天各渠道智能服务量是多少"
    """
    return await generate_metric_query(question)


__all__ = ["generate_dsl_json"]
