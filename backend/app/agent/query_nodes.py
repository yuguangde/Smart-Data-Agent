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
from app.agent.state import AgentState
from app.config import get_settings
from app.llm.factory import get_llm
from app.query._utils import _extract_json
from app.query.intent import UserIntent, classify_question
from app.query.nl2sql import generate_nl_sql

logger = logging.getLogger(__name__)


_ROUTER_SYSTEM_PROMPT = """\
你是 Smart Data Agent 的意图识别助手，只负责判断用户问题是否涉及数据分析或 SQL 查询。

数据问题包括：
- 指标、统计、趋势、聚合类问题（如“最近7天各渠道智能服务量是多少”）。
- 针对数据库表的自由探索（如“帮我看看最近有哪些用户登录过”）。
- 明显要求生成 SQL 或查询数据的请求。
- 对前一条数据查询的跟进，例如“按天拆分下”、“按渠道分组看看”、“继续细化”。

非数据问题包括：闲聊、问候、通用知识问答、不涉及任何表/指标的问题。

请 ONLY 输出一个 JSON 对象，不要带解释或 markdown 代码块：
{"is_data_question": true}
或
{"is_data_question": false}
"""


_FOLLOWUP_SYSTEM_PROMPT = """\
你是 Smart Data Agent 的指标查询改写助手。

上一轮用户已经通过语义层指标 DSL 完成了一个数据查询，上一轮的 DSL 如下：

```json
{previous_dsl}
```

上一轮生成的 SQL 如下：

```sql
{previous_sql}
```

本轮用户的跟进请求是：
{question}

请基于上一轮 DSL，在保持 dataset 和 metrics 不变的前提下，修改以下部分并生成新的 DSL JSON：
- 如果要求按天/按周/按月拆分，请添加对应的 dimensions 或设置 time_range.grain。
- 如果要求按某个维度分组，请在 dimensions 中添加该维度。
- 如果要求筛选、排序或限制，请调整 filters / order_by / limit。
- 如果要求对比不同时间范围，请调整 time_range.start / time_range.end。

请 ONLY 输出一个 JSON 对象，不要带解释或 markdown 代码块。DSL 必须符合 MetricQuery Schema。
"""


_SYNTHESIZE_SYSTEM_PROMPT = """\
你是 Smart Data Agent 的结果合成助手。你会收到一个 JSON 上下文，包含用户问题、数据意图、DSL（metric 场景）、SQL、以及执行结果或错误信息。

你的任务是根据上下文生成一个友好、专业、准确的中文最终回答。

规则：
1. 如果意图是 metric_analysis，先用 `json` 代码块展示 DSL，再用 `sql` 代码块展示 SQL。
2. 如果意图是 free_exploration，只需用 `sql` 代码块展示 SQL，不需要展示 DSL。
3. 如果执行成功，用 Markdown 表格展示 execution_result 中的关键数据（最多 20 行），并给出简短解读。
4. 如果执行失败或没有 execution_result，说明“SQL 执行不可用或失败”，展示 SQL 方便排查，不要编造数据。
5. 如果 DSL/SQL 生成失败，说明失败原因，并询问用户是否需要补充信息或换种方式提问。
6. 如果 is_data_question 为 false，则作为普通对话回答，不需要展示 SQL。

语气：友好、专业、不谄媚。
"""


_FOLLOWUP_KEYWORDS = {"拆分", "分组", "维度", "按", "继续", "细化", "趋势", "每天", "天", "周", "月", "渠道"}


def _last_user_message(state: AgentState) -> str:
    """Return the most recent HumanMessage content, or an empty string."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


def _second_last_user_message(state: AgentState) -> str:
    """Return the second most recent HumanMessage content, or empty string."""
    found_last = False
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            if not found_last:
                found_last = True
                continue
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


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
        previous = _second_last_user_message(state)
        if previous:
            context = f"\n\n前一条用户提问：{previous}"

    messages = [
        SystemMessage(content=_ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=f"当前用户提问：{question}{context}"),
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
            content=_FOLLOWUP_SYSTEM_PROMPT.format(
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
            sql = render_to_sql(new_dsl)
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
    """Generate SQL deterministically based on the user question.

    This node does not expose any tool to the LLM; it simply calls the
    existing DSL/nl2sql generators and writes the result into state.
    """
    question = _last_user_message(state)
    if not question:
        return {"query_error": "没有检测到用户问题"}

    # If a previous generation already failed, do not waste another LLM call.
    if state.get("query_error"):
        return {}

    update: dict[str, Any] = {"query_error": None}

    if state.get("is_metric_followup") and state.get("dsl"):
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


async def synthesize_node(state: AgentState) -> dict[str, Any]:
    """Synthesize the final answer from the data-pipeline state."""
    payload = {
        "question": _last_user_message(state),
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
        SystemMessage(content=_SYNTHESIZE_SYSTEM_PROMPT),
        HumanMessage(content=f"请根据以下上下文生成最终回答：\n\n{context}"),
    ]
    response = await get_llm().ainvoke(messages)
    answer = response.content if hasattr(response, "content") else str(response)

    return {
        "final_answer": answer,
        "messages": [AIMessage(content=answer)],
    }


__all__ = [
    "router_node",
    "generate_sql_node",
    "execute_sql_node",
    "synthesize_node",
]
