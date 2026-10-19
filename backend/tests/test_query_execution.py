"""Tests for the agent-driven data query execution tools.

The agent is the only LLM caller.  These tools only validate/render/execute the
DSL or SQL that the agent produced.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.query.registry import SemanticRegistry


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
    "\\\n-- MetricQuery: test_sales\n"
    'SELECT\n  region AS "region",\n  SUM(amount) AS "revenue"\n'
    "FROM db.sales_table\nWHERE 1=1\nGROUP BY\n  region\nLIMIT 100"
)

_FAKE_RESULT = {
    "columns": ["region", "revenue"],
    "rows": [["NORTH", 1200], ["SOUTH", 800]],
}

_FAKE_YAML = """
semantic_model:
  - name: test_model
    datasets:
      - name: test_sales
        source: db.sales_table
        dimensions:
          - region
        metrics:
          - name: revenue
            expression: amount
            default_agg: sum
"""


def _make_registry(yaml_content: str) -> SemanticRegistry:
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "test.ossie.yml"
    path.write_text(yaml_content, encoding="utf-8")
    registry = SemanticRegistry(ossie_dir=path.parent)
    registry._tmp = tmp  # type: ignore[attr-defined]
    return registry


@pytest.fixture(autouse=True)
def _reset_registry_singleton() -> None:
    """Drop any cached registry singleton before each test."""
    SemanticRegistry._instance = None
    yield
    SemanticRegistry._instance = None


@pytest.fixture
def _fake_registry() -> SemanticRegistry:
    registry = _make_registry(_FAKE_YAML)
    SemanticRegistry._instance = registry
    return registry


class _StagedFakeLLM:
    """Async LLM whose ainvoke returns a fixed sequence of responses."""

    def __init__(
        self,
        responses: list[str | tuple[str, list[dict[str, Any]]]],
    ) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def ainvoke(self, messages: list[Any], **kwargs: Any) -> AIMessage:
        if self._idx < len(self._responses):
            item = self._responses[self._idx]
            self._idx += 1
            if isinstance(item, tuple):
                content, tool_calls = item
            else:
                content, tool_calls = item, []
            return AIMessage(content=content, tool_calls=tool_calls)
        return AIMessage(content="Done.")

    def bind_tools(self, tools: list[Any]) -> "_StagedFakeLLM":
        return self


def _patch_llm_staged(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[str | tuple[str, list[dict[str, Any]]]],
) -> None:
    """Patch get_llm with a sequence of staged responses."""
    fake = _StagedFakeLLM(responses)
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda *args, **kwargs: fake)


async def _fake_execute_read_query(sql: str, db: str = "") -> dict[str, Any]:
    assert sql == _FAKE_SQL
    assert db == ""
    return _FAKE_RESULT


def test_data_tools_are_exposed_to_llm() -> None:
    """The agent should see the data context and execution tools."""
    from app.tools import get_llm_tools

    names = {t.name for t in get_llm_tools()}
    assert {
        "get_semantic_context",
        "execute_metric_dsl",
        "execute_sql",
    }.issubset(names)


@pytest.mark.anyio
async def test_get_semantic_context_tool_returns_context(
    _fake_registry: SemanticRegistry,
) -> None:
    """get_semantic_context should return the semantic layer summary."""
    from app.tools.semantic_layer import get_semantic_context

    result = await get_semantic_context.ainvoke({})
    assert "test_sales" in result
    assert "revenue" in result
    assert "region" in result


@pytest.mark.anyio
async def test_execute_metric_dsl_tool_validates_renders_and_executes(
    monkeypatch: pytest.MonkeyPatch,
    _fake_registry: SemanticRegistry,
) -> None:
    """execute_metric_dsl should validate DSL, render SQL, and execute it."""
    from app.tools.data_query import execute_metric_dsl

    monkeypatch.setattr(
        "app.agent.executor.execute_read_query",
        _fake_execute_read_query,
    )

    raw = await execute_metric_dsl.ainvoke({"dsl_json": json.dumps(_FAKE_DSL)})
    parsed = json.loads(raw)
    assert parsed["ok"]
    assert parsed["dsl"] == _FAKE_DSL
    assert _FAKE_SQL in parsed["sql"]
    assert parsed["result"] == _FAKE_RESULT


@pytest.mark.anyio
async def test_execute_metric_dsl_tool_rejects_bad_dsl(
    _fake_registry: SemanticRegistry,
) -> None:
    """execute_metric_dsl should return an error for invalid DSL."""
    from app.tools.data_query import execute_metric_dsl

    raw = await execute_metric_dsl.ainvoke({"dsl_json": "not valid json"})
    parsed = json.loads(raw)
    assert not parsed["ok"]
    assert "解析失败" in parsed["error"] or "JSON" in parsed["error"]


@pytest.mark.anyio
async def test_execute_sql_tool_runs_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_sql should run the provided SQL and return the result."""
    from app.tools.data_query import execute_sql

    monkeypatch.setattr(
        "app.agent.executor.execute_read_query",
        _fake_execute_read_query,
    )

    raw = await execute_sql.ainvoke({"sql": _FAKE_SQL})
    parsed = json.loads(raw)
    assert parsed["ok"]
    assert parsed["result"] == _FAKE_RESULT


@pytest.mark.anyio
async def test_agent_chains_execute_metric_dsl(
    monkeypatch: pytest.MonkeyPatch,
    _fake_registry: SemanticRegistry,
) -> None:
    """The agent should call execute_metric_dsl with a DSL JSON it produced."""
    from app.agent import graph as graph_mod
    from app.services.agent_service import invoke

    monkeypatch.setattr(
        "app.agent.executor.execute_read_query",
        _fake_execute_read_query,
    )

    dsl_json = json.dumps(_FAKE_DSL)
    _patch_llm_staged(
        monkeypatch,
        [
            (
                "Executing metric DSL.",
                [
                    {
                        "id": "call_1",
                        "name": "execute_metric_dsl",
                        "args": {"dsl_json": dsl_json},
                    }
                ],
            ),
            "最终答案：NORTH 1200，SOUTH 800。",
        ],
    )

    graph_mod.get_compiled_graph.cache_clear()

    result = await invoke(
        user_message="按 region 汇总 revenue",
        user_id="test",
        thread_id="test-agent-metric-dsl",
    )

    assert "NORTH" in result["message"]["content"]
    assert "1200" in result["message"]["content"]


__all__ = []
