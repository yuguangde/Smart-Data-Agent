"""DSL → SQL renderer using Jinja2 templates.

The renderer takes a validated :class:`MetricQuery` plus a resolved
:class:`SemanticDataset` and returns a deterministic SQL string. No LLM is
involved in the rendering step.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from jinja2 import BaseLoader, Environment

from app.query.dsl import Agg, Filter, FilterOp, Metric, MetricQuery, TimeGrain
from app.query.registry import SemanticDataset, SemanticMetric, SemanticRegistry


class DateDialect(StrEnum):
    """Target SQL dialect for date functions."""

    ANSI = "ansi"
    TRINO = "trino"
    HIVE = "hive"
    SPARK = "spark"


# ---------------------------------------------------------------------------
# Helpers for rendering values / operators
# ---------------------------------------------------------------------------

_AGG_RE = re.compile(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE)

# Java SimpleDateFormat → Python strftime mapping for common date parts.
_JAVA_TO_STRFTIME = {
    "yyyy": "%Y",
    "MM": "%m",
    "dd": "%d",
    "HH": "%H",
    "mm": "%M",
    "ss": "%S",
}


def _java_date_format_to_strftime(java_fmt: str) -> str:
    """Convert a Java/SimpleDateFormat pattern to Python strftime format."""
    result = java_fmt
    # Sort keys by length descending so multi-char patterns are replaced first.
    for java_pat, py_pat in sorted(_JAVA_TO_STRFTIME.items(), key=lambda x: -len(x[0])):
        result = result.replace(java_pat, py_pat)
    return result


def _format_date_for_dimension(iso_date: str, fmt: str | None) -> str:
    """Convert an ISO date (YYYY-MM-DD) to the target column format.

    If ``fmt`` is a Python strftime string (contains ``%``), use it directly.
    Otherwise treat it as a Java/SimpleDateFormat pattern.
    """
    if not fmt:
        return iso_date
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return iso_date
    py_fmt = fmt if "%" in fmt else _java_date_format_to_strftime(fmt)
    return dt.strftime(py_fmt)


def _quote(value: Any) -> str:
    """String-quote a scalar SQL value; numbers are passed through."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "NULL"
    # Escape single quotes by doubling them.
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _render_filter(f: Filter) -> str:
    """Render a single Filter predicate as SQL."""
    if f.op in (FilterOp.IN, FilterOp.NOT_IN):
        parts = ", ".join(_quote(v) for v in (f.values or []))
        op_sql = "IN" if f.op == FilterOp.IN else "NOT IN"
        return f"{f.field} {op_sql} ({parts})"

    op_sql = {
        FilterOp.EQ: "=",
        FilterOp.NE: "!=",
        FilterOp.GT: ">",
        FilterOp.GTE: ">=",
        FilterOp.LT: "<",
        FilterOp.LTE: "<=",
        FilterOp.LIKE: "LIKE",
    }[f.op]

    return f"{f.field} {op_sql} {_quote(f.value)}"


def _is_aggregate_expression(expr: str) -> bool:
    """Return True if ``expr`` already contains an aggregate function call."""
    return bool(_AGG_RE.search(expr))


def _apply_agg(expression: str, agg: Agg) -> str:
    """Wrap ``expression`` with the SQL aggregate function for ``agg``.

    ``count_distinct`` is rendered as ``COUNT(DISTINCT <expr>)``; all other
    aggregates are rendered as ``AGG(<expr>)``.
    """
    if agg == Agg.COUNT_DISTINCT:
        return f"COUNT(DISTINCT {expression})"
    return f"{agg.value.upper()}({expression})"


def _build_metric_expression(sm: SemanticMetric, metric: Metric) -> str:
    """Return the final SELECT expression for a metric.

    If the semantic metric already defines an aggregate expression, trust it
    and ignore the DSL-level ``agg``. Otherwise apply the requested aggregation
    to the metric expression.
    """
    expr = (sm.expression or "").strip()
    if not expr:
        raise RenderError(f"Metric '{metric.name}' has no expression")
    if _is_aggregate_expression(expr):
        return expr
    return _apply_agg(expr, metric.agg)


def _date_trunc_expr(field: str, grain: TimeGrain, dialect: DateDialect) -> str:
    """Return the expression that buckets a date field to a grain."""
    if grain == TimeGrain.DAY:
        return field

    if dialect in (DateDialect.TRINO, DateDialect.SPARK):
        unit = "WEEK" if grain == TimeGrain.WEEK else "MONTH"
        return f"DATE_TRUNC('{unit.lower()}', {field})"

    if dialect == DateDialect.HIVE:
        if grain == TimeGrain.WEEK:
            return f"DATE_TRUNC('{field}', 'YYYY-WW')"
        return f"DATE_TRUNC('{field}', 'YYYY-MM')"

    # Default ANSI-like syntax (StarRocks / MySQL style)
    if grain == TimeGrain.WEEK:
        return f"DATE_FORMAT({field}, '%Y-%u')"
    return f"DATE_FORMAT({field}, '%Y-%m')"


# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------

_SQL_TEMPLATE = r"""\
-- MetricQuery: {{ dataset.name }}
SELECT
{%- for dim in dimensions %}
  {{ dim[1] }} AS "{{ dim[0] }}"{% if not loop.last or metrics %},{% endif %}
{%- endfor %}
{%- for metric in metrics %}
  {{ metric.expression }} AS "{{ metric.name }}"{% if not loop.last %},{% endif %}
{%- endfor %}
FROM {{ dataset.source }}
WHERE 1=1
{%- if time_range %}
  AND {{ time_range.field }} >= {{ time_range.start | quote }}
  AND {{ time_range.field }} <= {{ time_range.end | quote }}
{%- endif %}
{%- for f in filters %}
  AND {{ f }}
{%- endfor %}
{%- for metric in metrics %}
{%- if metric.filter %}
  AND {{ metric.filter }}
{%- endif %}
{%- endfor %}
{%- if dimensions %}
GROUP BY
{%- for dim in dimensions %}
  {{ dim[1] }}{% if not loop.last %},{% endif %}
{%- endfor %}
{%- endif %}
{%- if order_by %}
ORDER BY
{%- for ob in order_by %}
  "{{ ob.field }}" {{ ob.dir.value }}{% if not loop.last %},{% endif %}
{%- endfor %}
{%- endif %}
LIMIT {{ limit }}
"""

_JINJA_ENV = Environment(loader=BaseLoader(), trim_blocks=False, lstrip_blocks=True)
_JINJA_ENV.filters["quote"] = _quote


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class RenderError(Exception):
    """Raised when a MetricQuery cannot be rendered to SQL."""


@dataclass(frozen=True)
class _RenderedMetric:
    """Internal intermediate representation of a metric in SQL form."""

    name: str
    expression: str
    filter: str | None


class MetricQueryRenderer:
    """Render a :class:`MetricQuery` to SQL for a given dialect."""

    def __init__(
        self,
        registry: SemanticRegistry | None = None,
        dialect: DateDialect | str = DateDialect.ANSI,
    ) -> None:
        self.registry = registry or SemanticRegistry.get()
        self.dialect = DateDialect(dialect)
        self._template = _JINJA_ENV.from_string(_SQL_TEMPLATE)

    def render(self, query: MetricQuery) -> str:
        """Return the SQL string for ``query``.

        Raises:
            RenderError: if the dataset or any metric is not found in the registry.
        """
        dataset = self.registry.get_dataset(query.dataset)
        if dataset is None:
            raise RenderError(f"Dataset '{query.dataset}' not found in semantic registry")

        rendered_metrics: list[_RenderedMetric] = []
        for metric in query.metrics:
            sm = self.registry.get_metric(metric.name)
            if sm is None:
                raise RenderError(f"Metric '{metric.name}' not found in semantic registry")
            rendered_metrics.append(
                _RenderedMetric(
                    name=sm.name,
                    expression=_build_metric_expression(sm, metric),
                    filter=sm.filter_,
                )
            )

        dimensions: list[tuple[str, str]] = []
        selected_dim_names = list(dict.fromkeys(query.dimensions))
        for dim_name in selected_dim_names:
            if dim_name not in dataset.dimension_names:
                # Best-effort: allow it if it is a selected metric name (rare case).
                if dim_name not in {rm.name for rm in rendered_metrics}:
                    raise RenderError(
                        f"Dimension '{dim_name}' not allowed for dataset '{dataset.name}'"
                    )
            expr = dim_name
            if (
                query.time_range
                and query.time_range.field == dim_name
                and query.time_range.grain
            ):
                expr = _date_trunc_expr(dim_name, query.time_range.grain, self.dialect)
            dimensions.append((dim_name, expr))

        # Validate that ORDER BY references known fields (metrics, dimensions,
        # or the time field). Keep the original OrderByItem objects for rendering.
        allowed_order_fields: set[str] = {rm.name for rm in rendered_metrics}
        allowed_order_fields |= {d[0] for d in dimensions}
        if query.time_range:
            allowed_order_fields.add(query.time_range.field)

        for ob in query.order_by:
            if ob.field not in allowed_order_fields:
                raise RenderError(
                    f"order_by.field='{ob.field}' cannot be rendered; "
                    f"allowed fields: {sorted(allowed_order_fields)}"
                )

        rendered_filters = [_render_filter(f) for f in query.filters]

        time_range_data: dict[str, Any] | None = None
        if query.time_range:
            time_dim = self.registry.get_dimension(
                query.dataset, query.time_range.field
            )
            target_format = time_dim.format if time_dim else None
            time_range_data = {
                "field": query.time_range.field,
                "start": _format_date_for_dimension(
                    query.time_range.start, target_format
                ),
                "end": _format_date_for_dimension(
                    query.time_range.end, target_format
                ),
            }

        return self._template.render(
            dataset=dataset,
            metrics=rendered_metrics,
            dimensions=dimensions,
            time_range=time_range_data,
            filters=rendered_filters,
            order_by=query.order_by,
            limit=query.limit,
        ).strip()


def render_to_sql(
    query: MetricQuery,
    *,
    registry: SemanticRegistry | None = None,
    dialect: DateDialect | str = DateDialect.ANSI,
) -> str:
    """Convenience function: render ``query`` directly to SQL."""
    return MetricQueryRenderer(registry=registry, dialect=dialect).render(query)


__all__ = [
    "DateDialect",
    "MetricQueryRenderer",
    "RenderError",
    "render_to_sql",
]
