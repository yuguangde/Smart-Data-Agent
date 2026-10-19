"""Human-in-the-loop review nodes for sensitive tool calls and SQL execution.

These nodes use LangGraph's ``interrupt`` primitive: when a sensitive action is
pending, the graph pauses and yields an approval payload. Resuming with
``Command(resume={"approved": True})`` lets the action proceed; otherwise the
pending calls are cancelled.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import interrupt

from app.agent.state import AgentState

SENSITIVE_TOOL_NAMES = {"read_file"}


def _last_ai_message(messages: list) -> AIMessage | None:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg
    return None


def _rewrite_ai_tool_calls(messages: list, tool_calls: list[dict[str, object]]) -> list:
    """Return a copy of ``messages`` with the last AIMessage tool_calls updated."""
    messages = list(messages)
    ai = _last_ai_message(messages)
    if ai is None:
        return messages
    for idx, msg in enumerate(messages):
        if isinstance(msg, AIMessage) and msg.id == ai.id:
            messages[idx] = AIMessage(
                content=ai.content,
                id=ai.id,
                tool_calls=tool_calls,
                additional_kwargs=ai.additional_kwargs,
            )
            break
    return messages


async def tool_review_node(state: AgentState) -> AgentState:
    """Pause the graph if any pending tool call is on the sensitive list.

    Unlike the original implementation, this node expects ``pending_tool_calls``
    to be populated by the upstream ``marshal_tools`` node. It restores the
    calls to the last assistant message on approval, or injects cancellation
    ``ToolMessage``s on denial.
    """
    pending = list(state.get("pending_tool_calls") or [])
    if not pending:
        return state

    # Pause for user approval. On resume, ``interrupt`` returns the value
    # passed by ``Command(resume={"approved": True/False})``.
    response = interrupt(
        {
            "type": "tool_approval",
            "tool_calls": pending,
            "message": (
                "Agent 请求读取本地文件。请确认是否允许执行以下工具调用？"
            ),
        }
    )

    approved = isinstance(response, dict) and response.get("approved") is True
    if approved:
        messages = _rewrite_ai_tool_calls(state.get("messages", []), pending)
        return {**state, "messages": messages, "pending_tool_calls": None}

    # Denied: inject cancellation ToolMessages and clear the pending calls.
    cancellation_messages = [
        ToolMessage(
            content="用户拒绝了此工具调用。",
            tool_call_id=tc.get("id"),
            name=tc.get("name"),
        )
        for tc in pending
        if tc.get("id")
    ]
    return {
        **state,
        "messages": list(state.get("messages", [])) + cancellation_messages,
        "pending_tool_calls": None,
    }


_SQL_REVIEW_MESSAGE = """系统已经为该数据问题生成了 SQL。

请确认是否允许执行该 SQL 以获取结果？

```sql
{sql}
```
"""


def data_review_node(state: AgentState) -> AgentState:
    """Pause the graph for human approval of the generated SQL.

    Unlike ``tool_review_node``, this node operates on the data-pipeline state
    fields and lets a user approve or deny the SQL before ``execute_sql_node``
    runs. It does not interrupt if SQL generation already failed.
    """
    sql = state.get("sql") or ""
    query_error = state.get("query_error")
    if not sql or query_error:
        return state

    response = interrupt(
        {
            "type": "sql_approval",
            "sql": sql,
            "message": _SQL_REVIEW_MESSAGE.format(sql=sql),
        }
    )

    approved = isinstance(response, dict) and response.get("approved") is True
    if approved:
        return state

    # Denied: mark the execution as blocked so the synthesizer can explain why.
    return {
        **state,
        "execution_error": "用户未授权执行该 SQL",
        "execution_result": None,
    }


__all__ = ["tool_review_node", "data_review_node", "SENSITIVE_TOOL_NAMES"]
