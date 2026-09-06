"""Tests for the metric query tool that generates DSL and renders SQL."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import app.query.tools as tools_mod
from app.query._utils import _extract_json
from app.query.intent import UserIntent
from app.query.registry import SemanticRegistry
from app.query.tools import generate_dsl_json, generate_sql


SAMPLE_YAML = """
semantic_model:
  - name: test_model
    datasets:
      - name: sales
        source: db.sales_table
        dimensions:
          - region
          - dt
        metrics:
          - name: revenue
            expression: amount
            default_agg: sum
          - name: 智能服务量
            expression: COUNT(DISTINCT router_id)
"""

VALID_DSL = {
    "dataset": "sales",
    "metrics": [{"name": "revenue", "agg": "sum"}],
    "dimensions": ["region"],
    "time_range": {
        "field": "dt",
        "start": "2026-08-01",
        "end": "2026-08-31",
        "grain": "day",
    },
    "limit": 100,
}


def _make_registry(yaml_content: str) -> SemanticRegistry:
    """Create a registry backed by a single temporary semantic model file."""
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "test.ossie.yml"
    path.write_text(yaml_content, encoding="utf-8")
    registry = SemanticRegistry(ossie_dir=path.parent)
    registry._tmp = tmp  # type: ignore[attr-defined]
    return registry


class _FakeLLM:
    """Async LLM whose ainvoke always returns the provided content."""

    def __init__(self, content: str) -> None:
        self.content = content

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        return AIMessage(content=self.content)


def _patch_generator_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    """Patch app.query.generator.get_llm to return a fake LLM."""
    from app.query import generator as generator_mod

    def fake_get_llm(*args: Any, **kwargs: Any) -> _FakeLLM:
        return _FakeLLM(content)

    monkeypatch.setattr(generator_mod, "get_llm", fake_get_llm)


def _patch_nl2sql_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    """Patch app.query.nl2sql.get_llm to return a fake LLM."""
    from app.query import nl2sql as nl2sql_mod

    def fake_get_llm(*args: Any, **kwargs: Any) -> _FakeLLM:
        return _FakeLLM(content)

    monkeypatch.setattr(nl2sql_mod, "get_llm", fake_get_llm)


@pytest.fixture
def sample_registry() -> SemanticRegistry:
    return _make_registry(SAMPLE_YAML)


@pytest.fixture(autouse=True)
def _reset_registry_singleton() -> None:
    """Drop any cached registry singleton before each test."""
    SemanticRegistry._instance = None
    yield
    SemanticRegistry._instance = None


@pytest.mark.anyio
async def test_generate_dsl_json_returns_dsl_and_sql(
    monkeypatch: pytest.MonkeyPatch,
    sample_registry: SemanticRegistry,
) -> None:
    _patch_generator_llm(monkeypatch, json.dumps(VALID_DSL, ensure_ascii=False))
    SemanticRegistry._instance = sample_registry

    result = json.loads(await generate_dsl_json.ainvoke({"question": "按 region 汇总 revenue"}))

    assert result["ok"] is True
    assert result["query"]["dataset"] == VALID_DSL["dataset"]
    assert result["query"]["metrics"] == VALID_DSL["metrics"]
    assert result["query"]["dimensions"] == VALID_DSL["dimensions"]
    assert "SELECT" in result["sql"]
    assert 'region AS "region"' in result["sql"]
    assert "SUM(amount) AS \"revenue\"" in result["sql"]
    assert "FROM db.sales_table" in result["sql"]


@pytest.mark.anyio
async def test_generate_dsl_json_sql_render_failure_keeps_dsl(
    monkeypatch: pytest.MonkeyPatch,
    sample_registry: SemanticRegistry,
) -> None:
    dsl_with_missing_dataset = {**VALID_DSL, "dataset": "missing_dataset"}
    _patch_generator_llm(monkeypatch, json.dumps(dsl_with_missing_dataset, ensure_ascii=False))
    SemanticRegistry._instance = sample_registry

    result = json.loads(await generate_dsl_json.ainvoke({"question": "missing dataset"}))

    assert result["ok"] is False
    assert result["query"]["dataset"] == "missing_dataset"
    assert result["stage"] == "sql_render"
    assert "missing_dataset" in result["error"]


@pytest.mark.anyio
async def test_generate_dsl_json_dsl_failure_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generator_llm(monkeypatch, "not valid json")

    result = json.loads(await generate_dsl_json.ainvoke({"question": "bad dsl"}))

    assert result["ok"] is False
    assert "error" in result
    assert "raw" in result


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"a": 1}', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('Some explanation\n```json\n{"a": 1}\n```\nMore text', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
    ],
)
def test_extract_json_handles_fences(raw: str, expected: str) -> None:
    assert _extract_json(raw) == expected


def _patch_settings(monkeypatch: pytest.MonkeyPatch, dialect: str) -> None:
    """Patch the settings singleton used by the tool."""
    from app.config import Settings

    monkeypatch.setattr(
        tools_mod,
        "get_settings",
        lambda: Settings(query_sql_dialect=dialect),
    )


DSL_WITH_MONTH_GRAIN = {
    "dataset": "sales",
    "metrics": [{"name": "revenue", "agg": "sum"}],
    "dimensions": ["dt"],
    "time_range": {
        "field": "dt",
        "start": "2026-08-01",
        "end": "2026-08-31",
        "grain": "month",
    },
    "limit": 100,
}


@pytest.mark.anyio
async def test_generate_dsl_json_respects_configured_dialect(
    monkeypatch: pytest.MonkeyPatch,
    sample_registry: SemanticRegistry,
) -> None:
    _patch_generator_llm(monkeypatch, json.dumps(DSL_WITH_MONTH_GRAIN, ensure_ascii=False))
    SemanticRegistry._instance = sample_registry
    _patch_settings(monkeypatch, "trino")

    result = json.loads(await generate_dsl_json.ainvoke({"question": "按月汇总 revenue"}))

    assert result["ok"] is True
    assert "DATE_TRUNC('month', dt)" in result["sql"]


# ---------------------------------------------------------------------------
# generate_sql routing tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_sql_routes_to_metric_when_keyword_present(
    monkeypatch: pytest.MonkeyPatch,
    sample_registry: SemanticRegistry,
) -> None:
    _patch_generator_llm(monkeypatch, json.dumps(VALID_DSL, ensure_ascii=False))
    SemanticRegistry._instance = sample_registry

    result = json.loads(await generate_sql.ainvoke({"question": "按 region 汇总 revenue"}))

    assert result["ok"] is True
    assert result["intent"] == UserIntent.METRIC_ANALYSIS.value
    assert result["query"]["dataset"] == "sales"
    assert "SELECT" in result["sql"]


@pytest.mark.anyio
async def test_generate_sql_routes_to_explore_when_no_keyword(
    monkeypatch: pytest.MonkeyPatch,
    sample_registry: SemanticRegistry,
) -> None:
    expected_sql = "SELECT * FROM db.sales_table LIMIT 5"
    _patch_nl2sql_llm(monkeypatch, json.dumps({"sql": expected_sql}, ensure_ascii=False))
    SemanticRegistry._instance = sample_registry

    result = json.loads(await generate_sql.ainvoke({"question": "帮我看看 sales 表里的样例数据"}))

    assert result["ok"] is True
    assert result["intent"] == UserIntent.FREE_EXPLORATION.value
    assert result["query"] is None
    assert result["sql"] == expected_sql


@pytest.mark.anyio
async def test_generate_sql_metric_error_includes_intent(
    monkeypatch: pytest.MonkeyPatch,
    sample_registry: SemanticRegistry,
) -> None:
    _patch_generator_llm(monkeypatch, "not valid json")
    SemanticRegistry._instance = sample_registry

    result = json.loads(await generate_sql.ainvoke({"question": "按 region 汇总 revenue"}))

    assert result["ok"] is False
    assert result["intent"] == UserIntent.METRIC_ANALYSIS.value
    assert "error" in result


__all__ = []
