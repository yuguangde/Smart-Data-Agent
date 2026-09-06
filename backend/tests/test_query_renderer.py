"""Tests for the metric query DSL → SQL renderer."""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from app.query.dsl import Agg, Filter, FilterOp, Metric, MetricQuery, OrderByItem, SortDir, TimeRange
from app.query.registry import SemanticRegistry
from app.query.renderer import DateDialect, MetricQueryRenderer, RenderError


def _make_registry(model_yaml: str) -> SemanticRegistry:
    """Create a registry backed by a single temporary semantic model file."""
    import tempfile

    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "test.ossie.yml"
    path.write_text(model_yaml, encoding="utf-8")
    registry = SemanticRegistry(ossie_dir=path.parent)
    # Stash the temp dir on the registry so it stays alive for the test.
    registry._tmp = tmp  # type: ignore[attr-defined]
    return registry


@pytest.fixture
def sample_registry() -> SemanticRegistry:
    yaml = """
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
          - name: order_count
            expression: order_id
            default_agg: count
          - name: high_value_orders
            expression: order_id
            default_agg: count
            filter: amount > 100
"""
    return _make_registry(yaml)


def _render(query_dict: dict[str, Any], registry: SemanticRegistry) -> str:
    query = MetricQuery.model_validate(query_dict)
    return MetricQueryRenderer(registry=registry, dialect=DateDialect.ANSI).render(query)


def test_basic_metric_no_dimensions(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "revenue", "agg": "sum"}],
        },
        sample_registry,
    )
    assert "SELECT" in sql
    assert "SUM(amount) AS \"revenue\"" in sql
    assert "FROM db.sales_table" in sql
    assert "LIMIT 100" in sql
    assert "GROUP BY" not in sql


def test_dimension_and_filter(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "revenue", "agg": "sum"}],
            "dimensions": ["region"],
            "filters": [{"field": "region", "op": "eq", "value": "NORTH"}],
            "limit": 50,
        },
        sample_registry,
    )
    assert 'region AS "region"' in sql
    assert "SUM(amount) AS \"revenue\"" in sql
    assert "WHERE 1=1" in sql
    assert "region = 'NORTH'" in sql
    assert "GROUP BY" in sql
    assert "region" in sql.split("GROUP BY")[1]
    assert "LIMIT 50" in sql


def test_aggregate_expression_ignores_dsl_agg(sample_registry: SemanticRegistry) -> None:
    """Semantic metrics whose expression is already aggregated must not be double-wrapped."""
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "unique_users", "agg": "sum"}],
        },
        sample_registry,
    )
    assert "COUNT(DISTINCT user_id) AS \"unique_users\"" in sql
    assert "SUM(" not in sql


def test_simple_expression_gets_dsl_agg(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "revenue", "agg": "avg"}],
        },
        sample_registry,
    )
    assert "AVG(amount) AS \"revenue\"" in sql


def test_count_distinct_agg(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "order_count", "agg": "count_distinct"}],
        },
        sample_registry,
    )
    assert "COUNT(DISTINCT order_id) AS \"order_count\"" in sql


def test_metric_filter_is_appended_to_where(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "high_value_orders", "agg": "count"}],
            "dimensions": ["region"],
        },
        sample_registry,
    )
    assert "amount > 100" in sql


def test_time_range_with_default_ansi_grain(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "revenue", "agg": "sum"}],
            "dimensions": ["dt"],
            "time_range": {
                "field": "dt",
                "start": "2026-08-01",
                "end": "2026-08-31",
                "grain": "month",
            },
        },
        sample_registry,
    )
    assert "DATE_FORMAT(dt, '%Y-%m') AS \"dt\"" in sql
    assert "dt >= '2026-08-01'" in sql
    assert "dt <= '2026-08-31'" in sql
    assert "GROUP BY" in sql
    assert "DATE_FORMAT(dt, '%Y-%m')" in sql.split("GROUP BY")[1]


def test_time_range_by_day_does_not_transform(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "revenue", "agg": "sum"}],
            "dimensions": ["dt"],
            "time_range": {
                "field": "dt",
                "start": "2026-08-01",
                "end": "2026-08-31",
                "grain": "day",
            },
        },
        sample_registry,
    )
    assert 'dt AS "dt"' in sql
    assert "DATE_FORMAT" not in sql


def test_order_by_metric(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "revenue", "agg": "sum"}],
            "dimensions": ["region"],
            "order_by": [{"field": "revenue", "dir": "desc"}],
        },
        sample_registry,
    )
    assert 'ORDER BY\n  "revenue" desc' in sql


def test_order_by_dimension(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "revenue", "agg": "sum"}],
            "dimensions": ["region"],
            "order_by": [{"field": "region", "dir": "asc"}],
        },
        sample_registry,
    )
    assert 'ORDER BY\n  "region" asc' in sql


@pytest.mark.parametrize(
    "dialect,expected_expr",
    [
        (DateDialect.TRINO, "DATE_TRUNC('month', dt)"),
        (DateDialect.SPARK, "DATE_TRUNC('month', dt)"),
        (DateDialect.HIVE, "DATE_TRUNC('dt', 'YYYY-MM')"),
        (DateDialect.ANSI, "DATE_FORMAT(dt, '%Y-%m')"),
    ],
)
def test_month_grain_dialects(
    sample_registry: SemanticRegistry,
    dialect: DateDialect,
    expected_expr: str,
) -> None:
    query = MetricQuery(
        dataset="sales",
        metrics=[Metric(name="revenue", agg=Agg.SUM)],
        dimensions=["dt"],
        time_range=TimeRange(field="dt", start="2026-08-01", end="2026-08-31", grain="month"),
    )
    sql = MetricQueryRenderer(registry=sample_registry, dialect=dialect).render(query)
    assert f"{expected_expr} AS \"dt\"" in sql


def test_dataset_not_found(sample_registry: SemanticRegistry) -> None:
    with pytest.raises(RenderError, match="Dataset 'missing' not found"):
        _render({"dataset": "missing", "metrics": [{"name": "revenue", "agg": "sum"}]}, sample_registry)


def test_metric_not_found(sample_registry: SemanticRegistry) -> None:
    with pytest.raises(RenderError, match="Metric 'missing' not found"):
        _render({"dataset": "sales", "metrics": [{"name": "missing", "agg": "sum"}]}, sample_registry)


def test_dimension_not_allowed(sample_registry: SemanticRegistry) -> None:
    with pytest.raises(RenderError, match="Dimension 'product' not allowed"):
        _render(
            {
                "dataset": "sales",
                "metrics": [{"name": "revenue", "agg": "sum"}],
                "dimensions": ["product"],
            },
            sample_registry,
        )


def test_order_by_not_allowed(sample_registry: SemanticRegistry) -> None:
    # Bypass the DSL-level order_by validation so we can exercise renderer-level checks.
    query = MetricQuery.model_construct(
        dataset="sales",
        metrics=[Metric(name="revenue", agg=Agg.SUM)],
        dimensions=["region"],
        order_by=[OrderByItem(field="product", dir=SortDir.DESC)],
    )
    renderer = MetricQueryRenderer(registry=sample_registry)
    with pytest.raises(RenderError, match="order_by.field='product'"):
        renderer.render(query)


def test_filter_in_operator(sample_registry: SemanticRegistry) -> None:
    sql = _render(
        {
            "dataset": "sales",
            "metrics": [{"name": "revenue", "agg": "sum"}],
            "filters": [{"field": "region", "op": "in", "values": ["NORTH", "SOUTH"]}],
        },
        sample_registry,
    )
    assert "region IN ('NORTH', 'SOUTH')" in sql
