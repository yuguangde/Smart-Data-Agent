"""Metric Query DSL core models.

This module defines the JSON-friendly, LLM-facing contract for metric-style
aggregation queries. All validation lives in Pydantic so that hallucinated
fields fail fast before any SQL is rendered.

Design constraints (v0):
- Only the "metric" query type is supported (grouped aggregation).
- All names are semantic names that must be resolved by a registry.
- The DSL is decoupled from SQL rendering: validation happens here, rendering
  is deterministic code elsewhere.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Scalar = Union[str, int, float]


class Agg(str, Enum):
    """Supported metric aggregations."""

    SUM = "sum"
    AVG = "avg"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    MAX = "max"
    MIN = "min"


class SortDir(str, Enum):
    """Sort direction."""

    ASC = "asc"
    DESC = "desc"


class FilterOp(str, Enum):
    """Filter operators; multi-value operators require ``values`` instead of ``value``."""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    LIKE = "like"


class TimeGrain(str, Enum):
    """Time bucketing granularity."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class Metric(BaseModel):
    """A requested metric: semantic name + aggregation."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="指标名，必须从指标字典中选择，禁止自创",
    )
    agg: Agg = Agg.SUM


class TimeRange(BaseModel):
    """Absolute time range plus optional grain for trend queries."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="时间字段名，来自该数据集的维度白名单",
    )
    start: str = Field(
        description="开始日期，格式 YYYY-MM-DD，相对时间需换算为绝对日期",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    end: str = Field(
        description="结束日期，格式 YYYY-MM-DD",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    grain: Optional[TimeGrain] = Field(
        None,
        description="填写则按该粒度分组展示趋势",
    )


class Filter(BaseModel):
    """A single filter predicate over a dimension field."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="维度字段名，来自维度白名单",
    )
    op: FilterOp
    value: Optional[Scalar] = None
    values: Optional[list[Scalar]] = None

    @model_validator(mode="after")
    def _check_arity(self) -> "Filter":
        """Multi-value ops require ``values``; single-value ops require ``value``."""
        if self.op in (FilterOp.IN, FilterOp.NOT_IN):
            if not self.values:
                raise ValueError(f"op={self.op.value} 必须提供 values 数组")
            self.value = None
        else:
            if self.value is None:
                raise ValueError(f"op={self.op.value} 必须提供 value")
            self.values = None
        return self


class OrderByItem(BaseModel):
    """Ordering clause referencing a metric or dimension field."""

    model_config = ConfigDict(extra="forbid")

    field: str
    dir: SortDir = SortDir.DESC


class MetricQuery(BaseModel):
    """Top-level v0 metric aggregation query.

    Example JSON:

        {
            "query_type": "metric",
            "dataset": "智能服务量表",
            "metrics": [
                {"name": "智能服务量", "agg": "count_distinct"}
            ],
            "dimensions": ["channel"],
            "time_range": {
                "field": "dt",
                "start": "2026-08-01",
                "end": "2026-08-31",
                "grain": "day"
            },
            "filters": [
                {"field": "channel", "op": "eq", "value": "MYPA"}
            ],
            "order_by": [
                {"field": "智能服务量", "dir": "desc"}
            ],
            "limit": 100
        }
    """

    model_config = ConfigDict(extra="forbid")

    query_type: Literal["metric"] = "metric"
    dataset: str = Field(
        description="数据集名，从数据集白名单中选择",
    )
    metrics: list[Metric] = Field(
        min_length=1,
        max_length=5,
        description="至少选一个指标，最多 5 个",
    )
    dimensions: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="分组维度，来自维度白名单",
    )
    time_range: Optional[TimeRange] = None
    filters: list[Filter] = Field(
        default_factory=list,
        max_length=5,
        description="普通筛选，恒为 AND 关系",
    )
    order_by: list[OrderByItem] = Field(
        default_factory=list,
        description="只能引用 metrics 或 dimensions 中出现过的字段",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="默认 100，上限 1000，防止全表拉取",
    )

    @model_validator(mode="after")
    def _cross_check(self) -> "MetricQuery":
        """Deduplicate dimensions and ensure order_by references valid fields."""
        self.dimensions = list(dict.fromkeys(self.dimensions))

        defined: set[str] = {m.name for m in self.metrics} | set(self.dimensions)
        if self.time_range:
            defined.add(self.time_range.field)

        for ob in self.order_by:
            if ob.field not in defined:
                raise ValueError(
                    f"order_by.field='{ob.field}' 不存在，"
                    f"只能引用 metrics 或 dimensions 中出现过的字段: {sorted(defined)}"
                )

        return self


def metric_query_json_schema(title: str = "metric_query") -> dict:
    """Return the JSON Schema for :class:`MetricQuery` in OpenAI function-calling shape."""
    schema = MetricQuery.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": title,
            "description": (
                "对数仓执行指标聚合分析查询，适用于 指标+维度+筛选+时间范围 形态的问题"
            ),
            "parameters": schema,
        },
    }


def metric_query_tools() -> list[dict]:
    """Convenience wrapper that returns a single-tool list for LLM tool calling."""
    return [metric_query_json_schema()]


__all__ = [
    "Scalar",
    "Agg",
    "SortDir",
    "FilterOp",
    "TimeGrain",
    "Metric",
    "TimeRange",
    "Filter",
    "OrderByItem",
    "MetricQuery",
    "metric_query_json_schema",
    "metric_query_tools",
]
