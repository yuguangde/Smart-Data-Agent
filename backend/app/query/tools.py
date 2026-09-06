"""LangChain tool wrappers for query routing.

These tools allow the agent to turn a natural-language data question into a
SQL string. ``generate_sql`` is the unified entry point: it classifies the
question as either metric analysis (controlled DSL) or free exploration
(LLM nl2sql) and returns the generated SQL.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from app.config import get_settings
from app.query.dsl import MetricQuery
from app.query.generator import generate_metric_query
from app.query.intent import UserIntent, classify_question
from app.query.nl2sql import generate_nl_sql
from app.query.renderer import RenderError, render_to_sql

logger = logging.getLogger(__name__)


def _serialize_payload(payload: dict[str, Any]) -> str:
    """Serialize a tool result so the LLM sees valid JSON."""
    return json.dumps(payload, ensure_ascii=False, default=str)


async def _generate_metric_sql(question: str) -> dict[str, Any]:
    """Internal metric-analysis path: DSL generation + deterministic SQL render.

    Returns a normalized dict with ``ok``, ``query`` (DSL dict or ``None``),
    ``sql`` (when successful), ``error``, and ``stage`` keys.
    """
    result = await generate_metric_query(question)

    if not result.get("ok"):
        return {
            "ok": False,
            "query": None,
            "error": result.get("error"),
            "stage": "dsl_generation",
            "raw": result.get("raw"),
        }

    dsl_dict = result["query"]
    try:
        query = MetricQuery.model_validate(dsl_dict)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Generated DSL failed re-validation: %s", exc)
        return {
            "ok": False,
            "query": dsl_dict,
            "error": f"DSL 校验失败: {exc}",
            "stage": "dsl_validation",
        }

    try:
        settings = get_settings()
        sql = render_to_sql(query, dialect=settings.query_sql_dialect)
    except RenderError as exc:
        return {
            "ok": False,
            "query": dsl_dict,
            "error": f"SQL 渲染失败: {exc}",
            "stage": "sql_render",
        }
    except ValueError as exc:
        # Invalid dialect config should be caught at startup, but guard anyway.
        logger.exception("Invalid SQL dialect configuration: %s", exc)
        return {
            "ok": False,
            "query": dsl_dict,
            "error": f"SQL 方言配置错误: {exc}",
            "stage": "sql_render",
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected error rendering SQL: %s", exc)
        return {
            "ok": False,
            "query": dsl_dict,
            "error": f"SQL 渲染异常: {exc}",
            "stage": "sql_render",
        }

    return {"ok": True, "query": dsl_dict, "sql": sql}


@tool
async def generate_dsl_json(question: str) -> str:
    """根据语义层配置，把自然语言指标分析问题转换为 MetricQuery DSL JSON 并渲染为 SQL。

    这是 ``generate_sql`` 的指标分析专用兼容入口，不绑定到工具列表中。

    返回的 JSON 字符串中：
    - ok=true 时包含 ``query``（DSL JSON）和 ``sql``（渲染后的 SQL）字段
    - ok=false 时包含 ``error`` 字段；若 DSL 已生成还会包含 ``query`` 和 ``stage``

    Args:
        question: 用户的自然语言指标分析问题，例如"最近7天各渠道智能服务量是多少"
    """
    result = await _generate_metric_sql(question)
    return _serialize_payload(result)


@tool
async def generate_sql(question: str) -> str:
    """根据用户意图，把自然语言数据问题转换为可执行 SQL。

    内部会先判断问题是“指标分析”还是“自由探索”：
    - 指标分析：使用受控的 MetricQuery DSL + 确定性 SQL 渲染，保证准确性；
    - 自由探索：使用 LLM nl2sql，灵活性更高。

    不执行 SQL 查询数据库。返回的 JSON 字符串中：
    - ok=true 时包含 ``intent``、``sql``；指标分析时还会包含 ``query``（DSL）
    - ok=false 时包含 ``error``、``stage``；可能包含 ``query``

    Args:
        question: 用户的自然语言数据问题，例如"最近7天各渠道智能服务量是多少"
    """
    intent = classify_question(question)

    if intent == UserIntent.METRIC_ANALYSIS:
        result = await _generate_metric_sql(question)
    else:
        settings = get_settings()
        nl_result = await generate_nl_sql(question, dialect=settings.query_sql_dialect)
        if nl_result.get("ok"):
            result: dict[str, Any] = {
                "ok": True,
                "query": None,
                "sql": nl_result["sql"],
            }
        else:
            result = {
                "ok": False,
                "query": None,
                "error": nl_result.get("error"),
                "stage": "nl2sql_generation",
                "raw": nl_result.get("raw"),
            }

    result["intent"] = intent.value
    return _serialize_payload(result)


__all__ = ["generate_dsl_json", "generate_sql"]
