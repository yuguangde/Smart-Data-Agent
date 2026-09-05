"""Semantic-layer registry for the metric query DSL.

Loads ``*.ossie.yml`` files from ``backend/ossie/`` and exposes the allowed
white-list of datasets, metrics, and dimensions that the LLM may reference.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SQL keywords / functions whose identifiers should not be treated as dimensions.
_DIMENSION_BLOCKLIST = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "distinct",
    "case",
    "when",
    "then",
    "else",
    "end",
    "if",
    "null",
    "and",
    "or",
    "not",
    "in",
    "is",
    "as",
    "from",
    "where",
    "select",
}


def _extract_candidate_columns(sql: str | None) -> list[str]:
    """Naively pull identifier-like tokens from a SQL snippet.

    This is only used as a fallback when a dataset does not declare its own
    ``dimensions``. The result is a *candidate* list that must still be vetted
    against a real schema in production.
    """
    if not sql:
        return []

    # Drop string literals and numeric literals so 'MYPA' / 1 / 0 are not
    # mistaken for dimension columns.
    cleaned = re.sub(r"'[^']*'", "", sql)
    cleaned = re.sub(r'"[^"]*"', "", cleaned)
    cleaned = re.sub(r"\b\d+\b", "", cleaned)

    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", cleaned)
    candidates = []
    for tok in tokens:
        lower = tok.lower()
        if lower in _DIMENSION_BLOCKLIST or lower in {"true", "false"}:
            continue
        if tok not in candidates:
            candidates.append(tok)
    return candidates


@dataclass
class SemanticMetric:
    """A metric defined in the semantic layer."""

    name: str
    expression: str
    filter_: str | None = None
    description: str | None = None
    default_agg: str = "sum"


@dataclass
class SemanticDataset:
    """A dataset (source table/view) exposed by the semantic layer."""

    name: str
    source: str
    model_name: str
    dimensions: list[str] = field(default_factory=list)
    metrics: list[SemanticMetric] = field(default_factory=list)

    @property
    def metric_names(self) -> list[str]:
        return [m.name for m in self.metrics]


@dataclass
class SemanticModel:
    """A semantic model; usually one per ``*.ossie.yml`` file."""

    name: str
    description: str | None = None
    datasets: list[SemanticDataset] = field(default_factory=list)


class SemanticRegistry:
    """In-memory registry of all loaded semantic layers.

    The registry scans a configurable directory (default ``backend/ossie/``)
    for ``*.ossie.yml`` files at import time. It is intentionally lightweight:
    semantic layers rarely change without a server restart.
    """

    _instance: "SemanticRegistry | None" = None

    def __init__(self, ossie_dir: Path | str | None = None) -> None:
        self._models: dict[str, SemanticModel] = {}
        self._datasets: dict[str, SemanticDataset] = {}
        self._metrics: dict[str, SemanticMetric] = {}
        self._dimensions: dict[str, set[str]] = {}

        # Allow override for tests; otherwise default to backend/ossie/.
        self._ossie_dir = Path(ossie_dir) if ossie_dir else _BASE_DIR / "ossie"
        self._load_all()

    @classmethod
    def get(cls) -> "SemanticRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_all(self) -> None:
        if not self._ossie_dir.exists():
            logger.warning("Semantic layer directory does not exist: %s", self._ossie_dir)
            return

        for path in sorted(self._ossie_dir.glob("*.ossie.yml")):
            try:
                self._load_file(path)
            except Exception as exc:
                logger.warning("Failed to load semantic layer %s: %s", path, exc)

    def _load_file(self, path: Path) -> None:
        with path.open("r", encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}

        for raw_model in payload.get("semantic_model", []):
            model_name = raw_model.get("name", path.stem)
            model = SemanticModel(
                name=model_name,
                description=raw_model.get("description"),
            )

            # Metrics can live at model level (current format) or dataset level.
            model_metrics = self._parse_metrics(raw_model.get("metrics", []))

            raw_datasets = raw_model.get("datasets", [])
            if isinstance(raw_datasets, dict):
                raw_datasets = [raw_datasets]

            for raw_ds in raw_datasets:
                ds_name = raw_ds.get("name", model_name)
                if "metrics" in raw_ds:
                    ds_metrics = self._parse_metrics(raw_ds["metrics"])
                else:
                    ds_metrics = list(model_metrics)
                declared_dims = raw_ds.get("dimensions") or []
                inferred_dims = self._infer_dimensions(ds_metrics)
                dimensions = [d for d in declared_dims if d] or inferred_dims

                dataset = SemanticDataset(
                    name=ds_name,
                    source=raw_ds.get("source", ""),
                    model_name=model_name,
                    dimensions=dimensions,
                    metrics=ds_metrics,
                )
                model.datasets.append(dataset)
                self._datasets[ds_name] = dataset

                for metric in ds_metrics:
                    # Names must be unique across the registry for white-listing.
                    if metric.name in self._metrics:
                        logger.warning(
                            "Duplicate metric name '%s' in dataset '%s'; overriding",
                            metric.name,
                            ds_name,
                        )
                    self._metrics[metric.name] = metric

                self._dimensions.setdefault(ds_name, set()).update(dimensions)

            self._models[model_name] = model

    def _parse_metrics(self, raw_metrics: list[dict[str, Any]]) -> list[SemanticMetric]:
        result: list[SemanticMetric] = []
        for raw in raw_metrics:
            expr = ""
            expr_block = raw.get("expression")
            if isinstance(expr_block, dict):
                dialects = expr_block.get("dialects", [])
                for d in dialects:
                    if d.get("dialect") == "ANSI_SQL":
                        expr = d.get("expression", "")
                        break
                if not expr and dialects:
                    expr = dialects[0].get("expression", "")
            elif isinstance(expr_block, str):
                expr = expr_block

            filter_ = raw.get("filter")
            if isinstance(filter_, dict):
                filter_ = " AND ".join(
                    f"{k}={v}" for k, v in filter_.items()
                )

            result.append(
                SemanticMetric(
                    name=raw.get("name", ""),
                    expression=expr,
                    filter_=filter_,
                    description=raw.get("description"),
                    default_agg=raw.get("default_agg", "sum"),
                )
            )
        return result

    def _infer_dimensions(self, metrics: list[SemanticMetric]) -> list[str]:
        """Infer candidate dimension fields from metric expressions/filters."""
        candidates: list[str] = []
        for m in metrics:
            combined = f"{m.expression or ''} {m.filter_ or ''}"
            for col in _extract_candidate_columns(combined):
                if col not in candidates:
                    candidates.append(col)
        return candidates

    # ------------------------------------------------------------------
    # Public lookup API
    # ------------------------------------------------------------------

    @property
    def models(self) -> dict[str, SemanticModel]:
        return dict(self._models)

    @property
    def datasets(self) -> dict[str, SemanticDataset]:
        return dict(self._datasets)

    @property
    def metrics(self) -> dict[str, SemanticMetric]:
        return dict(self._metrics)

    def get_dataset(self, name: str) -> SemanticDataset | None:
        return self._datasets.get(name)

    def get_metric(self, name: str) -> SemanticMetric | None:
        return self._metrics.get(name)

    def get_dimensions(self, dataset_name: str) -> set[str]:
        return set(self._dimensions.get(dataset_name, []))

    def is_valid_metric(self, name: str) -> bool:
        return name in self._metrics

    def is_valid_dimension(self, dataset_name: str, field: str) -> bool:
        return field in self._dimensions.get(dataset_name, set())

    def context_for_llm(self) -> str:
        """Return a concise plain-text description of available datasets/metrics/dims."""
        lines: list[str] = []
        for ds in self.datasets.values():
            lines.append(f"数据集: {ds.name} (source: {ds.source})")
            lines.append(f"  可用指标: {', '.join(ds.metric_names) or '(none)'}")
            lines.append(f"  可用维度: {', '.join(sorted(ds.dimensions)) or '(none)'}")
        return "\n".join(lines)


__all__ = [
    "SemanticRegistry",
    "SemanticModel",
    "SemanticDataset",
    "SemanticMetric",
]
