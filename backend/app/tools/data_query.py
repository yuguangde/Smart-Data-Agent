"""Agent-facing data query execution tools.

These tools do **not** call any LLM.  The Agent node is the only component that
uses an LLM; it directly produces either a MetricQuery DSL JSON (for metric-style
questions) or a SQL string (for free-form exploration), and these tools only
validate, render, and execute the payload.

Tool flow:

- Metric analysis: Agent LLM writes DSL JSON -> ``execute_metric_dsl``
- Free exploration: Agent LLM writes SQL -> ``execute_sql``
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from app.config import get_settings
from app.query.dsl import MetricQuery
from app.query.renderer import RenderError, render_to_sql

logger = logging.getLogger(__name__)


def _serialize(result: dict[str, Any]) -> str:
    """Serialize a tool result for the LLM."""
    return json.dumps(result, ensure_ascii=False, default=str)


@tool
async def execute_metric_dsl(dsl_json: str) -> str:
    """Validate a MetricQuery DSL JSON, render it to SQL, execute, and return the result.

    **本工具不调用 LLM。** Agent 需要在调用参数里直接提供合法的 MetricQuery DSL JSON。

    适用场景：指标分析、聚合统计、趋势/按维度分组类问题。

    Args:
        dsl_json: MetricQuery DSL JSON 字符串。Schema 示例见系统提示。

    Returns:
        JSON 字符串：ok=true 时包含 dsl、sql、result；ok=false 时包含 error。
    """
    try:
        dsl_dict = json.loads(dsl_json)
    except json.JSONDecodeError as exc:
        return _serialize({"ok": False, "error": f"DSL JSON 解析失败: {exc}"})

    try:
        query = MetricQuery.model_validate(dsl_dict)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("DSL validation failed: %s", exc)
        return _serialize(
            {"ok": False, "dsl": dsl_dict, "error": f"DSL 校验失败: {exc}"}
        )

    try:
        settings = get_settings()
        sql = render_to_sql(query, dialect=settings.query_sql_dialect)
    except RenderError as exc:
        return _serialize(
            {"ok": False, "dsl": dsl_dict, "error": f"SQL 渲染失败: {exc}"}
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected error rendering SQL: %s", exc)
        return _serialize(
            {"ok": False, "dsl": dsl_dict, "error": f"SQL 渲染异常: {exc}"}
        )

    from app.agent.executor import execute_read_query

    try:
        data = await execute_read_query(sql, db="")
        return _serialize({"ok": True, "dsl": dsl_dict, "sql": sql, "result": data})
    except RuntimeError as exc:
        logger.warning("execute_metric_dsl SQL execution skipped: %s", exc)
        return _serialize(
            {"ok": False, "dsl": dsl_dict, "sql": sql, "error": str(exc)}
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("execute_metric_dsl SQL execution error: %s", exc)
        return _serialize(
            {"ok": False, "dsl": dsl_dict, "sql": sql, "error": f"SQL 执行异常: {exc}"}
        )


@tool
async def execute_sql(sql: str) -> str:
    """Execute a read-only SQL query and return the result.

    **本工具不调用 LLM。** Agent 需要在调用参数里直接提供 SQL 字符串。

    适用场景：无法映射到已知语义指标的自由探索类问题。

    Args:
        sql: 要执行的 SQL 字符串。

    Returns:
        JSON 字符串：ok=true 时包含 sql、result；ok=false 时包含 error。
    """
    from app.agent.executor import execute_read_query

    try:
        data = await execute_read_query(sql, db="")
        return _serialize({"ok": True, "sql": sql, "result": data})
    except RuntimeError as exc:
        logger.warning("execute_sql SQL execution skipped: %s", exc)
        return _serialize({"ok": False, "sql": sql, "error": str(exc)})
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("execute_sql SQL execution error: %s", exc)
        return _serialize({"ok": False, "sql": sql, "error": f"SQL 执行异常: {exc}"})


__all__ = ["execute_metric_dsl", "execute_sql"]
