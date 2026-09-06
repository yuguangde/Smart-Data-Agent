"""Tests for the data question flow: generate_sql -> starrocks_read_query -> final answer."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool


# ----------- Fakes ---------------------------------------------------------

_FAKE_DSL = {
    "query_type": "metric",
    "dataset": "test_sales",
    "metrics": [{"name": "revenue", "agg": "sum"}],
    "dimensions": ["region"],
    "time_range": None,
    "filters": [],
    "order_by": [],
    "limit": 100,
}

_FAKE_SQL = (
    'SELECT\n  region AS "region",\n  SUM(amount) AS "revenue"\n'
    "FROM db.sales_table\nGROUP BY region\nLIMIT 100"
)

_FAKE_RESULT = {
    "columns": ["region", "revenue"],
    "rows": [["NORTH", 1200], ["SOUTH", 800]],
}


@tool
async def fake_generate_sql(question: str) -> str:
    """Fake unified SQL generator that returns a deterministic payload."""
    return json.dumps(
        {
            "ok": True,
            "intent": "metric_analysis",
            "query": _FAKE_DSL,
            "sql": _FAKE_SQL,
        },
        ensure_ascii=False,
    )


@tool
def fake_starrocks_read_query(query: str, db: str | None = None) -> str:
    """Fake StarRocks query executor."""
    return json.dumps(_FAKE_RESULT, ensure_ascii=False)


class _FakeLLM:
    """Scripted LLM that returns a fixed sequence of AIMessages."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._idx = 0

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        resp = self._responses[self._idx]
        self._idx += 1
        return AIMessage(
            content=resp.get("content", ""),
            tool_calls=resp.get("tool_calls", []),
        )

    def bind_tools(self, tools: list[Any]) -> "_FakeLLM":
        return self


# ----------- Prompt tests --------------------------------------------------

def test_system_prompt_mentions_data_query_rule() -> None:
    from app.agent.prompts import build_system_prompt

    prompt = build_system_prompt([fake_generate_sql])
    assert "SQL 生成与数据查询规则" in prompt
    assert "starrocks_read_query" in prompt
    assert "执行返回的 SQL" in prompt


# ----------- End-to-end sequence test --------------------------------------

@pytest.mark.anyio
async def test_metric_question_calls_generate_sql_then_starrocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent.graph as graph_mod
    import app.agent.nodes as nodes_mod
    from app.services.agent_service import invoke

    scripted_llm = _FakeLLM(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "fake_generate_sql",
                        "args": {"question": "按 region 汇总 revenue"},
                    }
                ],
            },
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "name": "fake_starrocks_read_query",
                        "args": {"query": _FAKE_SQL},
                    }
                ],
            },
            {
                "content": (
                    "以下是查询结果：\n\n"
                    "```json\n" + json.dumps(_FAKE_DSL, ensure_ascii=False) + "\n```\n\n"
                    "```sql\n" + _FAKE_SQL + "\n```\n\n"
                    "| region | revenue |\n|---|---|\n| NORTH | 1200 |\n| SOUTH | 800 |\n\n"
                    "NORTH 区域收入最高。"
                ),
            },
        ]
    )

    monkeypatch.setattr(
        nodes_mod,
        "get_all_tools",
        lambda: [fake_generate_sql, fake_starrocks_read_query],
    )
    monkeypatch.setattr(
        nodes_mod,
        "get_llm",
        lambda *args, **kwargs: scripted_llm,
    )

    # Clear the compiled-graph cache so the next invocation rebuilds with fakes.
    graph_mod.get_compiled_graph.cache_clear()

    result = await invoke(
        user_message="按 region 汇总 revenue",
        user_id="test",
        thread_id="test-thread",
    )

    tool_names = [tc["name"] for tc in result["tool_calls"]]
    assert tool_names == ["fake_generate_sql", "fake_starrocks_read_query"]

    content = result["message"]["content"]
    assert "```json" in content
    assert "```sql" in content
    assert "NORTH" in content
    assert _FAKE_SQL.splitlines()[1].strip() in content


__all__ = []
