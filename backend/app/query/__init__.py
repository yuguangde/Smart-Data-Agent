"""Query DSL and execution helpers.

``app.query`` contains the DSL models, the semantic registry, and the
deterministic SQL renderer used to answer structured data questions.
"""

from app.query.dsl import (
    Agg,
    Filter,
    FilterOp,
    Metric,
    MetricQuery,
    OrderByItem,
    Scalar,
    SortDir,
    TimeGrain,
    TimeRange,
    metric_query_json_schema,
)
from app.query.registry import (
    SemanticDataset,
    SemanticMetric,
    SemanticModel,
    SemanticRegistry,
)
from app.query.renderer import DateDialect, MetricQueryRenderer, RenderError, render_to_sql

__all__ = [
    "Agg",
    "DateDialect",
    "Filter",
    "FilterOp",
    "Metric",
    "MetricQuery",
    "MetricQueryRenderer",
    "OrderByItem",
    "RenderError",
    "Scalar",
    "SemanticDataset",
    "SemanticMetric",
    "SemanticModel",
    "SemanticRegistry",
    "SortDir",
    "TimeGrain",
    "TimeRange",
    "metric_query_json_schema",
    "render_to_sql",
]
