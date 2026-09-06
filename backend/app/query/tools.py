"""LangChain tool wrappers for the query DSL.

These tools allow the agent to convert a natural-language metric question into
a controlled DSL JSON and rendered SQL without querying the database.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from app.config import get_settings
from app.query.dsl import MetricQuery
from app.query.generator import generate_metric_query
from app.query.renderer import RenderError, render_to_sql

logger = logging.getLogger(__name__)


def _serialize_payload(payload: dict[str, Any]) -> str:
    """Serialize a tool result so the LLM sees valid JSON."""
    return json.dumps(payload, ensure_ascii=False, default=str)


@tool
async def generate_dsl_json(question: str) -> str:
    """根据语义层配置，把自然语言指标分析问题转换为 MetricQuery DSL JSON 并渲染为 SQL。

    生成并校验 DSL，随后使用配置的目标方言（ansi/trino/spark/hive）渲染为 SQL。
    不执行 SQL 查询数据库。返回的 JSON 字符串中：
    - ok=true 时包含 ``query``（DSL JSON）和 ``sql``（渲染后的 SQL）字段
    - ok=false 时包含 ``error`` 字段；若 DSL 已生成还会包含 ``query`` 和 ``stage``

    Args:
        question: 用户的自然语言指标分析问题，例如"最近7天各渠道智能服务量是多少"
    """
    result = await generate_metric_query(question)

    if not result.get("ok"):
        return _serialize_payload(result)

    dsl_dict = result["query"]
    try:
        query = MetricQuery.model_validate(dsl_dict)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Generated DSL failed re-validation: %s", exc)
        return _serialize_payload(
            {"ok": False, "error": f"DSL 校验失败: {exc}", "stage": "dsl_validation"}
        )

    try:
        settings = get_settings()
        sql = render_to_sql(query, dialect=settings.query_sql_dialect)
    except RenderError as exc:
        return _serialize_payload(
            {
                "ok": False,
                "query": dsl_dict,
                "error": f"SQL 渲染失败: {exc}",
                "stage": "sql_render",
            }
        )
    except ValueError as exc:
        # Invalid dialect config should be caught at startup, but guard anyway.
        logger.exception("Invalid SQL dialect configuration: %s", exc)
        return _serialize_payload(
            {
                "ok": False,
                "query": dsl_dict,
                "error": f"SQL 方言配置错误: {exc}",
                "stage": "sql_render",
            }
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected error rendering SQL: %s", exc)
        return _serialize_payload(
            {
                "ok": False,
                "query": dsl_dict,
                "error": f"SQL 渲染异常: {exc}",
                "stage": "sql_render",
            }
        )

    return _serialize_payload({"ok": True, "query": dsl_dict, "sql": sql})


__all__ = ["generate_dsl_json"]
