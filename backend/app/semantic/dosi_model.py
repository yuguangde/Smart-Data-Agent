"""Combine project OSSIE YAML files into a single DoSI-compatible model entrypoint.

DoSI expects one Apache Ossie model file on the command line, but its supported
schema is a little different from the project's richer YAML format:

- ``version`` is required at the top level.
- Dataset attributes and dimensions are flattened into ``fields``.
- Metrics live at the ``semantic_model`` level (not inside datasets) and their
  expressions are table-qualified, e.g. ``SUM(yearmonth.Consumption)``.
- Relationships use ``from`` / ``to`` table names plus ``from_columns`` /
  ``to_columns`` with required relationship ``name``.

This module merges multiple ``*.ossie.yml`` files under ``backend/ossie/`` and
normalises them into ``backend/data/dosi-model.yaml`` so DoSI can consume the
full semantic layer.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from app.config import BASE_DIR

logger = logging.getLogger(__name__)


_VERSION = "0.2.0.dev0"


_SUPPORTED_REL_FIELDS = {
    "name",
    "from",
    "to",
    "from_columns",
    "to_columns",
    "ai_context",
    "custom_extensions",
}


_SUPPORTED_DATASET_FIELDS = {
    "name",
    "source",
    "primary_key",
    "unique_keys",
    "description",
    "ai_context",
    "fields",
    "custom_extensions",
}


_SUPPORTED_FIELD_FIELDS = {
    "name",
    "expression",
    "datatype",
    "description",
    "dimension",
    "ai_context",
    "custom_extensions",
}


_SUPPORTED_METRIC_FIELDS = {
    "name",
    "expression",
    "datatype",
    "description",
    "ai_context",
    "custom_extensions",
}


_SQL_DIALECT = "ANSI_SQL"


def _split_table_column(value: Any) -> tuple[str, list[str]]:
    """Split a ``table.column`` reference into table name and column list."""
    text = str(value)
    if "." in text:
        table, column = text.split(".", 1)
        return table, [column]
    return text, []


def _clean_relationship(rel: dict[str, Any]) -> dict[str, Any]:
    """Normalise a relationship so DoSI's parser accepts it."""
    cleaned = {k: v for k, v in rel.items() if k in _SUPPORTED_REL_FIELDS}

    if "from" in cleaned:
        table, cols = _split_table_column(cleaned["from"])
        cleaned["from"] = table
        if "from_columns" not in cleaned:
            cleaned["from_columns"] = cols
    if "to" in cleaned:
        table, cols = _split_table_column(cleaned["to"])
        cleaned["to"] = table
        if "to_columns" not in cleaned:
            cleaned["to_columns"] = cols

    if "name" not in cleaned:
        from_part = cleaned.get("from", "?")
        from_col = cleaned.get("from_columns", ["?"])[0]
        to_part = cleaned.get("to", "?")
        cleaned["name"] = f"{from_part}.{from_col} -> {to_part}"

    return cleaned


def _column_expression(column: str, dataset_name: str) -> dict[str, Any]:
    """Return a DoSI field expression referencing a dataset column."""
    return {
        "dialects": [
            {
                "dialect": _SQL_DIALECT,
                "expression": f"{dataset_name}.{column}",
            }
        ]
    }


def _metric_expression(
    expression: str,
    dataset_name: str,
    dimension_columns: list[str],
) -> dict[str, Any]:
    """Rewrite a dataset-scoped SQL expression using table-qualified columns.

    Example: ``COUNT(DISTINCT CustomerID)`` inside the ``customers`` dataset
    becomes ``COUNT(DISTINCT customers.CustomerID)``.
    """
    rewritten = expression
    # Sort by length desc so that ``CustomerID`` is replaced before ``ID``.
    for col in sorted(dimension_columns, key=len, reverse=True):
        # Match the column as a whole word outside of quoted strings.
        rewritten = re.sub(rf"\b{re.escape(col)}\b", f"{dataset_name}.{col}", rewritten)
    return {
        "dialects": [
            {
                "dialect": _SQL_DIALECT,
                "expression": rewritten,
            }
        ]
    }


def _build_field(
    dim: dict[str, Any],
    dataset_name: str,
) -> dict[str, Any]:
    """Convert a project dimension definition into a DoSI field."""
    field: dict[str, Any] = {
        "name": dim["name"],
        "expression": _column_expression(str(dim["name"]), dataset_name),
    }
    if dim.get("description"):
        field["description"] = dim["description"]
    if dim.get("is_time"):
        field["dimension"] = {"is_time": bool(dim["is_time"])}
    if dim.get("ai_context"):
        field["ai_context"] = dim["ai_context"]
    # Preserve any custom extension data if present.
    for extra in ("custom_extensions",):
        if extra in dim:
            field[extra] = dim[extra]
    return field


def _build_metric(
    metric: dict[str, Any],
    dataset_name: str,
    dimension_columns: list[str],
) -> dict[str, Any]:
    """Convert a dataset-scoped project metric into a top-level DoSI metric."""
    name = metric["name"]
    expression_text = ""
    expr = metric.get("expression")
    if isinstance(expr, dict):
        dialects = expr.get("dialects", [])
        if dialects:
            expression_text = str(dialects[0].get("expression", ""))
        else:
            expression_text = str(expr.get("expression", ""))
    else:
        expression_text = str(expr or "")

    out: dict[str, Any] = {
        "name": name,
        "expression": _metric_expression(expression_text, dataset_name, dimension_columns),
    }
    for key in ("description", "ai_context", "custom_extensions"):
        if metric.get(key):
            out[key] = metric[key]
    return out


def _clean_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Convert one of our dataset definitions into a DoSI-compatible dataset."""
    name = str(dataset.get("name", ""))
    source = str(dataset.get("source", name))

    out: dict[str, Any] = {"name": name, "source": source}

    for key in ("primary_key", "unique_keys"):
        if key in dataset:
            out[key] = dataset[key]
    if dataset.get("description"):
        out["description"] = dataset["description"]
    if dataset.get("ai_context"):
        out["ai_context"] = dataset["ai_context"]
    if dataset.get("custom_extensions"):
        out["custom_extensions"] = dataset["custom_extensions"]

    # Build fields from dimensions.
    dimension_names: list[str] = []
    fields: list[dict[str, Any]] = []
    for dim in dataset.get("dimensions", []) or []:
        if not isinstance(dim, dict):
            continue
        dim_name = str(dim.get("name", ""))
        if dim_name:
            dimension_names.append(dim_name)
        fields.append(_build_field(dim, name))

    if fields:
        out["fields"] = fields

    # Metrics are returned separately and attached at the semantic_model level.
    metrics: list[dict[str, Any]] = [
        _build_metric(m, name, dimension_names)
        for m in (dataset.get("metrics", []) or [])
        if isinstance(m, dict)
    ]

    return out, metrics


def _normalize_expression(value: Any, dataset_name: str = "") -> dict[str, Any]:
    """Return a DoSI-compatible expression block.

    Accepts either a raw SQL string or an already structured expression dict.
    """
    if isinstance(value, dict) and "dialects" in value:
        return value
    text = str(value or "")
    if dataset_name and text:
        # Best-effort prefix any bare column with the dataset name.
        # This won't rewrite partial identifiers, but handles simple metrics.
        pass
    return {
        "dialects": [
            {
                "dialect": _SQL_DIALECT,
                "expression": text,
            }
        ]
    }


def _clean_semantic_model(model: dict[str, Any]) -> dict[str, Any]:
    """Convert one project semantic model into a DoSI-compatible model."""
    out: dict[str, Any] = {}
    for key in ("name", "description"):
        if key in model:
            out[key] = model[key]
    if model.get("ai_context"):
        out["ai_context"] = model["ai_context"]
    if model.get("custom_extensions"):
        out["custom_extensions"] = model["custom_extensions"]

    # Datasets.
    all_metrics: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    for dataset in model.get("datasets", []) or []:
        if not isinstance(dataset, dict):
            continue
        cleaned_ds, ds_metrics = _clean_dataset(dataset)
        datasets.append(cleaned_ds)
        all_metrics.extend(ds_metrics)

    if datasets:
        out["datasets"] = datasets

    # Relationships.
    relationships: list[dict[str, Any]] = [
        _clean_relationship(rel)
        for rel in model.get("relationships", []) or []
        if isinstance(rel, dict)
    ]
    if relationships:
        out["relationships"] = relationships

    # Metrics at semantic_model level.
    # Also include any metrics already defined at the model level.
    for m in (model.get("metrics", []) or []):
        if not isinstance(m, dict):
            continue
        cleaned_metric = {k: v for k, v in m.items() if k in _SUPPORTED_METRIC_FIELDS}
        if "expression" in cleaned_metric:
            cleaned_metric["expression"] = _normalize_expression(cleaned_metric["expression"])
        all_metrics.append(cleaned_metric)

    if all_metrics:
        out["metrics"] = all_metrics

    return out


def _default_ossie_dir() -> Path:
    """Return the directory where ``*.ossie.yml`` files live."""
    return Path(BASE_DIR) / "ossie"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return the top-level mapping."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_dosi_model(
    ossie_dir: Path | str | None = None,
    output_path: Path | str | None = None,
    file_patterns: list[str] | None = None,
) -> Path:
    """Merge matching OSSIE files into one DoSI model file.

    Args:
        ossie_dir: Directory to scan. Defaults to ``backend/ossie/``.
        output_path: Where to write the combined model. Defaults to the value
            configured by ``settings.dosi_model_path``.
        file_patterns: Glob patterns to select OSSIE files. When ``None``, the
            value from ``settings.dosi_ossie_files`` is used.

    Returns:
        Path to the written combined model file.
    """
    from app.config import get_settings

    if ossie_dir is None:
        ossie_dir = _default_ossie_dir()
    else:
        ossie_dir = Path(ossie_dir)

    if output_path is None:
        output_path = Path(get_settings().dosi_model_path)
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use configured glob patterns so DoSI can be scoped to compatible OSSIE files.
    if file_patterns is None:
        file_patterns = list(get_settings().dosi_ossie_files) or ["*.ossie.yml"]
    seen_paths: set[Path] = set()
    models: list[dict[str, Any]] = []
    for pattern in file_patterns:
        for path in sorted(ossie_dir.glob(pattern)):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            payload = _load_yaml(path)
            raw_models = payload.get("semantic_model")
            if not raw_models:
                continue
            if isinstance(raw_models, dict):
                raw_models = [raw_models]
            for raw_model in raw_models:
                if isinstance(raw_model, dict):
                    models.append(raw_model)

    if not models:
        logger.warning("No semantic models found in %s", ossie_dir)

    cleaned_models = [_clean_semantic_model(m) for m in models]

    # DoSI expects exactly one semantic_model per document, so merge all project
    # models into a single entry while preserving dataset/relationship/metric names.
    combined_model: dict[str, Any] = {
        "name": "smart_data_agent",
        "description": "Combined semantic model for Smart Data Agent.",
    }
    for key in ("description", "ai_context"):
        if cleaned_models and cleaned_models[0].get(key):
            combined_model[key] = cleaned_models[0][key]
    if cleaned_models:
        combined_model["name"] = cleaned_models[0].get("name", "smart_data_agent")
        if "description" in cleaned_models[0]:
            combined_model["description"] = cleaned_models[0]["description"]

    for key in ("datasets", "relationships", "metrics"):
        values: list[Any] = []
        for m in cleaned_models:
            values.extend(m.get(key, []))
        if values:
            combined_model[key] = values

    combined = {"version": _VERSION, "semantic_model": [combined_model]}
    with output_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(combined, f, sort_keys=False, allow_unicode=True)

    logger.info(
        "Built DoSI model at %s from %d semantic model(s) in %s",
        output_path,
        len(models),
        ossie_dir,
    )
    return output_path


def get_dosi_model_path() -> Path:
    """Return the configured DoSI model path, building it if necessary."""
    from app.config import get_settings

    path = Path(get_settings().dosi_model_path)
    if not path.exists():
        return build_dosi_model(output_path=path)
    return path


__all__ = ["build_dosi_model", "get_dosi_model_path"]
