"""Query DSL and execution helpers.

``app.query`` contains the LLM-facing DSL models, the semantic registry, and
the deterministic SQL renderer used to answer structured data questions.
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
    metric_query_tools,
)
from app.query.generator import generate_for_dataset, generate_metric_query
from app.query.registry import (
    SemanticDataset,
    SemanticMetric,
    SemanticModel,
    SemanticRegistry,
)

__all__ = [
    "Agg",
    "Filter",
    "FilterOp",
    "Metric",
    "MetricQuery",
    "OrderByItem",
    "Scalar",
    "SemanticDataset",
    "SemanticMetric",
    "SemanticModel",
    "SemanticRegistry",
    "SortDir",
    "TimeGrain",
    "TimeRange",
    "generate_for_dataset",
    "generate_metric_query",
    "metric_query_json_schema",
    "metric_query_tools",
]
