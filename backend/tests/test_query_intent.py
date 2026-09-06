"""Tests for the simple keyword-based intent classifier."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.query.intent import UserIntent, classify_question
from app.query.registry import SemanticRegistry


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
          - name: unique_users
            expression: COUNT(DISTINCT user_id)
          - name: 智能服务量
            expression: |
              COUNT(DISTINCT CASE WHEN channel IN ('MYPA') THEN router_id END)
"""


def _make_registry(yaml_content: str) -> SemanticRegistry:
    """Create a registry backed by a single temporary semantic model file."""
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


def test_metric_keyword_english() -> None:
    registry = _make_registry(SAMPLE_YAML)
    SemanticRegistry._instance = registry
    assert classify_question("按 region 汇总 revenue") == UserIntent.METRIC_ANALYSIS


def test_metric_keyword_chinese() -> None:
    registry = _make_registry(SAMPLE_YAML)
    SemanticRegistry._instance = registry
    assert classify_question("最近7天各渠道智能服务量是多少") == UserIntent.METRIC_ANALYSIS


def test_free_exploration_when_no_metric_keyword() -> None:
    registry = _make_registry(SAMPLE_YAML)
    SemanticRegistry._instance = registry
    assert classify_question("帮我看看表里有哪些字段") == UserIntent.FREE_EXPLORATION


def test_custom_registry_override() -> None:
    registry = _make_registry(SAMPLE_YAML)
    assert classify_question("查询 unique_users", registry=registry) == UserIntent.METRIC_ANALYSIS
    assert classify_question("随便看看", registry=registry) == UserIntent.FREE_EXPLORATION


__all__ = []
