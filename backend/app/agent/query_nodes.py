"""Data-pipeline nodes for program-driven SQL generation and execution.

These nodes are wired into the LangGraph by ``app.agent.graph``. They do not
appear in the LLM-visible tool list; the LLM only interacts with the final
synthesized answer.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.executor import execute_read_query
from app.agent.prompts import (
    DATA_REFLECTION_SYSTEM_PROMPT,
    FINAL_SYNTHESIZE_SYSTEM_PROMPT,
    FOLLOWUP_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    SYNTHESIZE_SYSTEM_PROMPT,
)
from app.agent.state import AgentState
from app.config import get_settings
from app.llm.factory import get_llm
from app.query._utils import _extract_json
from app.query.intent import UserIntent, classify_question
from app.query.nl2sql import generate_nl_sql

logger = logging.getLogger(__name__)


_FOLLOWUP_KEYWORDS = {
    "拆分",
    "分组",
    "维度",
    "按",
    "继续",
    "细化",
    "趋势",
    "每天",
    "天",
    "周",
    "月",
    "渠道",
    # Chart / visualization follow-ups on the previous metric result.
    "图",
    "折线",
    "绘制",
    "画",
}


def _last_user_message(state: AgentState) -> str:
    """Return the most recent HumanMessage content, or an empty string."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def _current_data_question(state: AgentState) -> str:
    """Return the active data question.

    During a multi-step analysis loop, ``refined_question`` takes precedence
    over the last user message so the next SQL targets the planned sub-query.
    """
    return state.get("refined_question") or _last_user_message(state)


def _format_collected_results(results: list[dict[str, Any]]) -> str:
    """Format collected execution results for LLM prompts."""
    if not results:
        return "暂无"
    lines: list[str] = []
    for idx, item in enumerate(results, 1):
        lines.append(f"\n[查询 {idx}] {item.get('question', '未知问题')}")
        if item.get("sql"):
            lines.append(f"SQL: {item.get('sql')}")
        lines.append(f"结果: {json.dumps(item.get('result'), ensure_ascii=False, default=str)[:500]}")
    return "\n".join(lines)


def _recent_conversation_transcript(
    state: AgentState,
    max_messages: int = 8,
) -> str:
    """Return a concise transcript of the recent conversation for context.

    Includes both user and assistant messages so the router LLM can judge
    intent based on the full context, not just the last question.
    """
    messages = state.get("messages", [])[-max_messages:]
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            role = "用户"
        elif isinstance(msg, AIMessage):
            # Skip long synthesized answers in the transcript; keep it short.
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if len(content) > 200:
                content = content[:200] + "…"
            role = "助手"
        else:
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if content:
            lines.append(f"{role}: {content[:400]}")
    return "\n".join(lines)


def _parse_is_data_question(raw: str) -> bool:
    """Parse the router LLM output and return a boolean."""
    cleaned = _extract_json(raw)
    try:
        parsed = json.loads(cleaned)
        return bool(parsed.get("is_data_question", False))
    except json.JSONDecodeError:
        logger.warning("Router returned invalid JSON: %s", cleaned)
        return False


def _has_metric_followup_keywords(question: str) -> bool:
    """Return True if the question looks like a metric-analysis follow-up."""
    if len(question) > 60:
        return False
    return any(kw in question for kw in _FOLLOWUP_KEYWORDS)


def _is_metric_followup(state: AgentState, question: str) -> bool:
    """Heuristic: is this a short follow-up to a previous metric query?"""
    previous_intent = state.get("intent")
    previous_dsl = state.get("dsl")
    if previous_intent != UserIntent.METRIC_ANALYSIS.value or not previous_dsl:
        return False
    return _has_metric_followup_keywords(question)


async def _classify_with_llm(question: str, state: AgentState | None = None) -> bool:
    """Ask a lightweight LLM whether the question is data-related.

    When ``state`` is provided, the prompt includes recent conversation context
    so short follow-ups like "按天拆分下" can be correctly classified.
    """
    context = ""
    if state is not None:
        transcript = _recent_conversation_transcript(state)
        if transcript:
            context = f"\n\n最近对话上下文：\n{transcript}"

    messages = [
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=f"当前用户最新提问：{question}{context}"),
    ]
    # Simple retry: two attempts to parse a valid JSON boolean.
    for attempt in range(2):
        response = await get_llm().ainvoke(messages)
        raw = response.content if hasattr(response, "content") else str(response)
        is_data = _parse_is_data_question(raw)
        # If we got a clean JSON object, trust it immediately.
        cleaned = _extract_json(raw)
        try:
            json.loads(cleaned)
            return is_data
        except json.JSONDecodeError:
            if attempt == 1:
                logger.warning("Router LLM returned malformed JSON after retry: %s", raw)
    return False


async def router_node(state: AgentState) -> dict[str, Any]:
    """Determine whether the latest user message is a data question.

    Considers both the current message and the conversation context, so short
    follow-ups like "按天拆分下" are recognized as data questions when the
    previous turn was a metric query.
    """
    question = _last_user_message(state)
    if not question:
        return {"is_data_question": False}

    # Fast path: question mentions a metric defined in the semantic layer.
    if classify_question(question) == UserIntent.METRIC_ANALYSIS:
        logger.debug("Router fast path: metric keyword matched")
        return {"is_data_question": True, "is_metric_followup": False}

    # Context heuristic: short follow-up to a previous metric query.
    if _is_metric_followup(state, question):
        logger.debug("Router heuristic: metric-analysis follow-up detected")
        return {"is_data_question": True, "is_metric_followup": True}

    # Fallback: ask a lightweight LLM classifier with context.
    is_data = await _classify_with_llm(question, state)
    logger.debug("Router LLM decided is_data_question=%s", is_data)
    return {
        "is_data_question": is_data,
        "is_metric_followup": False,
    }


async def _generate_metric_sql(question: str) -> dict[str, Any]:
    """Patch-friendly wrapper around app.query.tools._generate_metric_sql.

    Importing the helper inside the function lets tests monkeypatch
    ``app.agent.query_nodes._generate_metric_sql`` without worrying about
    module-level import binding.
    """
    from app.query.tools import _generate_metric_sql as _impl

    return await _impl(question)


async def _generate_metric_followup(
    question: str,
    previous_dsl: dict[str, Any],
    previous_sql: str,
) -> dict[str, Any]:
    """Generate a new metric DSL by modifying the previous one based on the follow-up."""
    from app.query.renderer import render_to_sql

    previous_dsl_json = json.dumps(previous_dsl, ensure_ascii=False, indent=2)
    messages = [
        SystemMessage(
            content=FOLLOWUP_SYSTEM_PROMPT.format(
                previous_dsl=previous_dsl_json,
                previous_sql=previous_sql or "",
                question=question,
            )
        ),
    ]

    for attempt in range(2):
        response = await get_llm().ainvoke(messages)
        raw = response.content if hasattr(response, "content") else str(response)
        cleaned = _extract_json(raw)
        try:
            new_dsl = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Follow-up DSL generation returned invalid JSON: %s", raw)
            if attempt == 1:
                return {
                    "ok": False,
                    "query": previous_dsl,
                    "sql": previous_sql,
                    "error": "跟进 DSL 生成失败：无法解析 JSON",
                    "stage": "followup_dsl_generation",
                    "raw": raw,
                }
            continue

        try:
            from app.query.dsl import MetricQuery

            # Normalize dimensions to strings. Some LLMs emit {"name": ..., "grain": ...}
            # objects instead of plain dimension names.
            dims: list[Any] = list(new_dsl.get("dimensions") or [])
            normalized_dims: list[str] = []
            for dim in dims:
                if isinstance(dim, str):
                    normalized_dims.append(dim)
                elif isinstance(dim, dict):
                    name = dim.get("name")
                    if isinstance(name, str):
                        normalized_dims.append(name)

            # Ensure time_range.field appears in dimensions when grain is set,
            # otherwise the renderer will not GROUP BY it.
            time_range = new_dsl.get("time_range")
            if time_range and time_range.get("grain"):
                field = time_range.get("field")
                if field and field not in normalized_dims:
                    normalized_dims.append(field)
            new_dsl["dimensions"] = normalized_dims

            # Normalize order_by: accept both {"field": ..., "dir": ...} and the
            # malformed {"field": ..., "order": ...} produced by some models.
            order_by: list[Any] = list(new_dsl.get("order_by") or [])
            normalized_order_by: list[dict[str, Any]] = []
            for ob in order_by:
                if isinstance(ob, str):
                    parts = ob.split()
                    normalized_order_by.append(
                        {"field": parts[0], "dir": parts[1] if len(parts) > 1 else "asc"}
                    )
                elif isinstance(ob, dict):
                    item = dict(ob)
                    if "order" in item and "dir" not in item:
                        item["dir"] = item.pop("order")
                    if "field" in item:
                        normalized_order_by.append(item)
            new_dsl["order_by"] = normalized_order_by

            query = MetricQuery.model_validate(new_dsl)
            sql = render_to_sql(query)
            return {"ok": True, "query": new_dsl, "sql": sql}
        except Exception as exc:
            logger.warning("Follow-up DSL render failed: %s", exc)
            if attempt == 1:
                return {
                    "ok": False,
                    "query": new_dsl,
                    "sql": previous_sql,
                    "error": f"跟进 DSL 渲染失败: {exc}",
                    "stage": "followup_dsl_render",
                    "raw": raw,
                }

    # Should never reach here.
    return {
        "ok": False,
        "query": previous_dsl,
        "sql": previous_sql,
        "error": "跟进 DSL 生成失败",
        "stage": "followup_dsl_generation",
    }


async def generate_sql_node(state: AgentState) -> dict[str, Any]:
    """Generate SQL deterministically based on the active data question.

    This node does not expose any tool to the LLM; it simply calls the
    existing DSL/nl2sql generators and writes the result into state.

    During a multi-step analysis loop, the active question comes from
    ``state["refined_question"]`` rather than the last user message.
    """
    question = _current_data_question(state)
    if not question:
        return {"query_error": "没有检测到用户问题"}

    # If a previous generation already failed, do not waste another LLM call.
    if state.get("query_error"):
        return {}

    update: dict[str, Any] = {"query_error": None}

    # If this is a fresh data question (not a continuation of the multi-step
    # reflection loop), reset loop state so that stale data from earlier turns
    # does not leak into the new analysis. ``refined_question`` is deliberately
    # left untouched when set, because data_reflection_node needs it to know
    # which sub-query was actually executed in the current iteration.
    if not state.get("refined_question"):
        update.update(
            {
                "collected_results": [],
                "data_iterations": 0,
                "data_sufficient": None,
                "refined_question": None,
            }
        )

    # Multi-step loop: if a refined_question was set by reflection, treat it as
    # a fresh query through the normal routing (it already contains full intent).
    if state.get("refined_question"):
        intent = classify_question(question)
        update["intent"] = intent.value
        if intent == UserIntent.METRIC_ANALYSIS:
            result = await _generate_metric_sql(question)
        else:
            settings = get_settings()
            nl_result = await generate_nl_sql(
                question,
                dialect=settings.query_sql_dialect,
            )
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
    elif state.get("is_metric_followup") and state.get("dsl"):
        update["intent"] = UserIntent.METRIC_ANALYSIS.value
        result = await _generate_metric_followup(
            question,
            state["dsl"],
            state.get("sql") or "",
        )
    else:
        intent = classify_question(question)
        update["intent"] = intent.value

        if intent == UserIntent.METRIC_ANALYSIS:
            result = await _generate_metric_sql(question)
        else:
            settings = get_settings()
            nl_result = await generate_nl_sql(
                question,
                dialect=settings.query_sql_dialect,
            )
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

    if result.get("ok"):
        update.update(
            {
                "sql": result["sql"],
                "dsl": result.get("query"),
                "query_error": None,
            }
        )
    else:
        update.update(
            {
                "sql": None,
                "dsl": result.get("query"),
                "query_error": result.get("error"),
            }
        )

    return update


async def execute_sql_node(state: AgentState) -> dict[str, Any]:
    """Programmatic SQL execution node. No LLM is involved here."""
    sql = state.get("sql")
    query_error = state.get("query_error")

    if query_error:
        logger.debug("Skipping SQL execution because query generation failed")
        return {}
    if not sql:
        logger.debug("Skipping SQL execution because sql is empty")
        return {}

    try:
        data = await execute_read_query(sql, db="")
        return {"execution_result": data, "execution_error": None}
    except RuntimeError as exc:
        logger.warning("SQL execution skipped: %s", exc)
        return {"execution_result": None, "execution_error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected SQL execution error: %s", exc)
        return {"execution_result": None, "execution_error": f"SQL 执行异常: {exc}"}


async def data_reflection_node(state: AgentState) -> dict[str, Any]:
    """Decide whether the collected data is sufficient or more queries are needed.

    Appends the latest execution result to ``collected_results`` and asks a
    lightweight LLM to plan the next step. This enables multi-step data analysis
    where a single SQL is not enough to answer the user's original question.
    """
    original_question = _last_user_message(state)
    last_question = _current_data_question(state)
    last_sql = state.get("sql") or ""
    last_result = state.get("execution_result") or state.get("execution_error") or "无结果"

    collected: list[dict[str, Any]] = list(state.get("collected_results") or [])
    collected.append(
        {
            "question": last_question,
            "sql": last_sql,
            "result": last_result,
        }
    )

    iterations = state.get("data_iterations", 0) + 1

    prompt = DATA_REFLECTION_SYSTEM_PROMPT.format(
        original_question=original_question,
        collected_results=_format_collected_results(collected),
        last_question=last_question,
        last_sql=last_sql,
        last_result=json.dumps(last_result, ensure_ascii=False, default=str),
    )

    for attempt in range(2):
        response = await get_llm().ainvoke([SystemMessage(content=prompt)])
        raw = response.content if hasattr(response, "content") else str(response)
        cleaned = _extract_json(raw)
        try:
            parsed = json.loads(cleaned)
            data_sufficient = bool(parsed.get("data_sufficient", False))
            refined_question = parsed.get("refined_question")
            if not data_sufficient and not refined_question:
                # If the model says insufficient but gives no next question,
                # force final synthesis to avoid an empty loop.
                data_sufficient = True
            return {
                "collected_results": collected,
                "data_iterations": iterations,
                "data_sufficient": data_sufficient,
                "refined_question": refined_question if not data_sufficient else None,
            }
        except json.JSONDecodeError:
            logger.warning("Data reflection returned invalid JSON: %s", raw)
            if attempt == 1:
                return {
                    "collected_results": collected,
                    "data_iterations": iterations,
                    "data_sufficient": True,
                    "refined_question": None,
                }

    # Should never reach here.
    return {
        "collected_results": collected,
        "data_iterations": iterations,
        "data_sufficient": True,
        "refined_question": None,
    }


async def synthesize_node(state: AgentState) -> dict[str, Any]:
    """Synthesize the final answer from all collected data-pipeline results."""
    original_question = _last_user_message(state)
    collected = state.get("collected_results") or []

    # Fallback: if the multi-step loop never ran, synthesize from the single
    # current result for backwards compatibility.
    if not collected and (state.get("sql") or state.get("execution_result")):
        payload = {
            "question": original_question,
            "is_data_question": state.get("is_data_question"),
            "intent": state.get("intent"),
            "dsl": state.get("dsl"),
            "sql": state.get("sql"),
            "query_error": state.get("query_error"),
            "execution_result": state.get("execution_result"),
            "execution_error": state.get("execution_error"),
        }
        context = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
        messages = [
            SystemMessage(content=SYNTHESIZE_SYSTEM_PROMPT),
            HumanMessage(content=f"请根据以下上下文生成最终回答：\n\n{context}"),
        ]
    else:
        context = f"""\
用户原始问题：
{original_question}

已执行的多步查询及结果：
{_format_collected_results(collected)}
"""
        messages = [
            SystemMessage(content=FINAL_SYNTHESIZE_SYSTEM_PROMPT),
            HumanMessage(content=context),
        ]

    response = await get_llm().ainvoke(messages)
    answer = response.content if hasattr(response, "content") else str(response)

    return {
        "final_answer": answer,
        "messages": [AIMessage(content=answer)],
        # Clear the loop cursor so a fresh user question on the same thread
        # does not accidentally resume the reflection loop.
        "refined_question": None,
    }


__all__ = [
    "router_node",
    "generate_sql_node",
    "execute_sql_node",
    "data_reflection_node",
    "synthesize_node",
]
