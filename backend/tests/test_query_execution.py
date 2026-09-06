"""Tests for the program-driven data-pipeline: generate SQL -> execute -> synthesize."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.query.registry import SemanticRegistry


def _patch_query_nodes_generate_metric_sql(
    monkeypatch: pytest.MonkeyPatch,
    result: dict[str, Any],
) -> None:
    """Patch the query_nodes module-level wrapper so the graph sees the fake."""
    import app.agent.query_nodes as qn

    async def fake(question: str) -> dict[str, Any]:
        return result

    monkeypatch.setattr(qn, "_generate_metric_sql", fake)


class _FakeLLM:
    """Fake LLM used to avoid real network calls during graph tests."""

    def __init__(self, content: str) -> None:
        self.content = content

    async def ainvoke(self, messages: list[Any], **kwargs: Any) -> AIMessage:
        return AIMessage(content=self.content)

    def bind_tools(self, tools: list[Any]) -> "_FakeLLM":
        return self


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    """Patch get_llm in all modules that call it during the data pipeline."""
    fake = _FakeLLM(content)
    monkeypatch.setattr("app.agent.query_nodes.get_llm", lambda *args, **kwargs: fake)
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda *args, **kwargs: fake)


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


async def _fake_generate_metric_sql(_question: str) -> dict[str, Any]:
    return {"ok": True, "query": _FAKE_DSL, "sql": _FAKE_SQL}


async def _fake_execute_read_query(sql: str, db: str = "") -> dict[str, Any]:
    assert sql == _FAKE_SQL
    assert db == ""
    return _FAKE_RESULT


@pytest.mark.anyio
async def test_data_pipeline_executes_sql_and_synthesizes_answer(
    monkeypatch: pytest.MonkeyPatch,
    _fake_registry: SemanticRegistry,
) -> None:
    """The graph should run generate -> execute -> synthesize without LLM deciding execution."""
    from app.agent import graph as graph_mod
    from app.services.agent_service import invoke

    _patch_query_nodes_generate_metric_sql(
        monkeypatch,
        {"ok": True, "query": _FAKE_DSL, "sql": _FAKE_SQL},
    )
    monkeypatch.setattr(
        "app.agent.executor.execute_read_query",
        _fake_execute_read_query,
    )
    _patch_llm(
        monkeypatch,
        content=f"SQL: {_FAKE_SQL}\n\n结果：NORTH 1200，SOUTH 800",
    )

    graph_mod.get_compiled_graph.cache_clear()

    result = await invoke(
        user_message="按 region 汇总 revenue",
        user_id="test",
        thread_id="test-data-pipeline",
    )

    content = result["message"]["content"]
    assert _FAKE_SQL in content
    assert "NORTH" in content
    assert "1200" in content


@pytest.mark.anyio
async def test_data_pipeline_handles_execution_error(
    monkeypatch: pytest.MonkeyPatch,
    _fake_registry: SemanticRegistry,
) -> None:
    """If SQL execution fails, the synthesizer should explain the error without crashing."""
    from app.agent import graph as graph_mod
    from app.services.agent_service import invoke

    async def failing_executor(_sql: str, _db: str = "") -> dict[str, Any]:
        raise RuntimeError("connection refused")

    _patch_query_nodes_generate_metric_sql(
        monkeypatch,
        {"ok": True, "query": _FAKE_DSL, "sql": _FAKE_SQL},
    )
    monkeypatch.setattr(
        "app.agent.executor.execute_read_query",
        failing_executor,
    )
    _patch_llm(
        monkeypatch,
        content=f"SQL 执行失败：connection refused\n\nSQL：{_FAKE_SQL}",
    )

    graph_mod.get_compiled_graph.cache_clear()

    result = await invoke(
        user_message="按 region 汇总 revenue",
        user_id="test",
        thread_id="test-exec-error",
    )

    content = result["message"]["content"]
    assert _FAKE_SQL in content
    assert "执行" in content or "失败" in content or "不可用" in content


@pytest.mark.anyio
async def test_data_pipeline_skips_execution_on_generation_error(
    monkeypatch: pytest.MonkeyPatch,
    _fake_registry: SemanticRegistry,
) -> None:
    """When SQL generation fails, execute_sql_node should not be reached."""
    from app.agent import graph as graph_mod
    from app.services.agent_service import invoke

    executor_called = False

    async def tracking_executor(_sql: str, _db: str = "") -> dict[str, Any]:
        nonlocal executor_called
        executor_called = True
        return _FAKE_RESULT

    _patch_query_nodes_generate_metric_sql(
        monkeypatch,
        {"ok": False, "query": None, "error": "无法识别指标"},
    )
    monkeypatch.setattr(
        "app.agent.executor.execute_read_query",
        tracking_executor,
    )
    _patch_llm(monkeypatch, content="生成失败，请补充信息")

    graph_mod.get_compiled_graph.cache_clear()

    result = await invoke(
        user_message="按 region 汇总 revenue",
        user_id="test",
        thread_id="test-gen-error",
    )

    assert not executor_called
    assert "失败" in result["message"]["content"] or "补充" in result["message"]["content"]


@pytest.mark.anyio
async def test_data_pipeline_hil_approval_runs_sql(
    monkeypatch: pytest.MonkeyPatch,
    _fake_registry: SemanticRegistry,
) -> None:
    """With HITL enabled, approving the SQL review should execute the query."""
    from app.agent import graph as graph_mod
    from app.config import get_settings
    from app.services.agent_service import invoke

    _patch_query_nodes_generate_metric_sql(
        monkeypatch,
        {"ok": True, "query": _FAKE_DSL, "sql": _FAKE_SQL},
    )
    monkeypatch.setattr(
        "app.agent.executor.execute_read_query",
        _fake_execute_read_query,
    )
    _patch_llm(
        monkeypatch,
        content=f"SQL: {_FAKE_SQL}\n\n结果：NORTH 1200，SOUTH 800",
    )

    settings = get_settings()
    monkeypatch.setattr(settings, "hitl", True)

    graph_mod.get_compiled_graph.cache_clear()

    thread_id = "test-hil-approved"
    first = await invoke(
        user_message="按 region 汇总 revenue",
        user_id="test",
        thread_id=thread_id,
    )
    assert first.get("pending_approval")
    assert first["pending_approval"]["type"] == "sql_approval"
    assert _FAKE_SQL in first["pending_approval"]["sql"]

    resumed = await invoke(
        user_message="",
        thread_id=thread_id,
        resume={"approved": True},
    )
    content = resumed["message"]["content"]
    assert _FAKE_SQL in content
    assert "NORTH" in content


@pytest.mark.anyio
async def test_data_pipeline_hil_denial_skips_execution(
    monkeypatch: pytest.MonkeyPatch,
    _fake_registry: SemanticRegistry,
) -> None:
    """With HITL enabled, denying the SQL review should synthesize a denial answer."""
    from app.agent import graph as graph_mod
    from app.config import get_settings
    from app.services.agent_service import invoke

    executor_called = False

    async def tracking_executor(_sql: str, _db: str = "") -> dict[str, Any]:
        nonlocal executor_called
        executor_called = True
        return _FAKE_RESULT

    _patch_query_nodes_generate_metric_sql(
        monkeypatch,
        {"ok": True, "query": _FAKE_DSL, "sql": _FAKE_SQL},
    )
    monkeypatch.setattr(
        "app.agent.executor.execute_read_query",
        tracking_executor,
    )
    _patch_llm(
        monkeypatch,
        content="用户未授权执行该 SQL，因此无法返回数据。",
    )

    settings = get_settings()
    monkeypatch.setattr(settings, "hitl", True)

    graph_mod.get_compiled_graph.cache_clear()

    thread_id = "test-hil-denied"
    first = await invoke(
        user_message="按 region 汇总 revenue",
        user_id="test",
        thread_id=thread_id,
    )
    assert first.get("pending_approval")
    assert first["pending_approval"]["type"] == "sql_approval"

    resumed = await invoke(
        user_message="",
        thread_id=thread_id,
        resume={"approved": False},
    )
    assert not executor_called
    assert "未授权" in resumed["message"]["content"] or "无法" in resumed["message"]["content"]


__all__ = []
