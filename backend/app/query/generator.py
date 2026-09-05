"""DSL generator: turn a natural-language metric question into a MetricQuery JSON.

This module deliberately does **not** query the database. It only produces a
validated DSL object that can be returned to the user (or later handed to a SQL
renderer).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.llm.factory import get_llm
from app.query.dsl import MetricQuery, metric_query_json_schema
from app.query.registry import SemanticDataset, SemanticRegistry

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
你是 Smart Data Agent 的指标查询助手。你的任务是：把用户的自然语言问题转换成一个受控的 JSON DSL。

规则：
1. 你只能使用语义层中明确定义的数据集、指标和维度（见下文白名单）。禁止编造任何字段或指标。
2. 如果用户问到的指标不在白名单里，不要自己推断 SQL 或看字段，必须拒绝并说明“当前语义层没有定义该指标，请补充语义层定义”。
3. 时间范围要转换为绝对日期（YYYY-MM-DD）。如果用户说“最近7天”，以今天为基准计算起止日期。
4. 生成的 DSL 必须能通过 Pydantic 校验；校验失败的字段会回传给你修正。
5. 只输出 JSON DSL，不要输出解释文字。

可用语义层：
{semantic_context}

输出必须符合以下 JSON Schema：
{schema}
"""


def _dataset_context(dataset: SemanticDataset) -> str:
    """Build LLM context scoped to a single dataset."""
    lines = [
        f"数据集: {dataset.name} (source: {dataset.source})",
        f"  可用指标: {', '.join(dataset.metric_names) or '(none)'}",
        f"  可用维度: {', '.join(sorted(dataset.dimensions)) or '(none)'}",
    ]
    return "\n".join(lines)


def _build_system_message(semantic_context: str) -> SystemMessage:
    schema = metric_query_json_schema()
    content = SYSTEM_PROMPT.format(
        semantic_context=semantic_context,
        schema=json.dumps(schema, ensure_ascii=False, indent=2),
    )
    return SystemMessage(content=content)


def _build_user_prompt(question: str) -> HumanMessage:
    return HumanMessage(content=f"用户问题：{question}\n\n请生成对应的 DSL JSON：")


def _serialize_validation_error(exc: ValidationError) -> str:
    """Convert a Pydantic ValidationError into an LLM-friendly string."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"  - {loc}: {err['msg']}")
    return "\n".join(parts)


def _extract_json(raw: str) -> str:
    """Strip markdown code fences if the model wrapped JSON in ```json ... ```."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines() if not line.startswith("```")
        ).strip()
    return cleaned


async def generate_metric_query(
    question: str,
    *,
    max_retries: int = 2,
    semantic_context: str | None = None,
) -> dict[str, Any]:
    """Generate a validated :class:`MetricQuery` JSON for ``question``.

    The function makes up to ``max_retries + 1`` LLM calls. If Pydantic
    validation fails, the error text is fed back to the model so it can fix
    the DSL. The returned dict is either:

    - ``{"ok": True, "query": <MetricQuery dict>}``
    - ``{"ok": False, "error": <str>, "raw": <raw model output>}``
    """
    if semantic_context is None:
        semantic_context = SemanticRegistry.get().context_for_llm()

    messages = [
        _build_system_message(semantic_context),
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
                        f"上一次的 JSON 校验失败，请修复后重新生成：\n{last_error}\n"
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
            logger.warning("DSL generation attempt %d produced invalid JSON", attempt + 1)
            continue

        try:
            query = MetricQuery.model_validate(parsed)
            return {"ok": True, "query": query.model_dump()}
        except ValidationError as exc:
            last_error = _serialize_validation_error(exc)
            logger.warning(
                "DSL generation attempt %d failed validation: %s",
                attempt + 1,
                last_error,
            )
            continue

    return {
        "ok": False,
        "error": f"无法生成合法 DSL（已重试 {max_retries} 次）。最后一次错误：\n{last_error}",
        "raw": last_raw,
    }


async def generate_for_dataset(
    question: str,
    dataset: SemanticDataset,
    *,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Convenience variant that scopes the context to a single dataset."""
    return await generate_metric_query(
        question,
        max_retries=max_retries,
        semantic_context=_dataset_context(dataset),
    )


__all__ = ["generate_metric_query", "generate_for_dataset"]
