"""Combine project OSSIE YAML files into a single DoSI-compatible model entrypoint.

DoSI expects one Apache Ossie model file on the command line. The project keeps
multiple ``*.ossie.yml`` files under ``backend/ossie/``. This module merges them
into one combined YAML at startup so DoSI can consume the full semantic layer.

The OSSIE files are assumed to be already DoSI-compatible (``fields`` instead of
``dimensions``, metrics at the ``semantic_model`` level, relationships without
``type``/``description``). The only transformations performed here are:

- Merge multiple ``semantic_model`` entries into **one** (DoSI allows exactly one
  per document).
- Strip unsupported fields from relationships if they still carry legacy ones
  (``type``, ``description``), and auto-generate a relationship ``name``.
"""
from __future__ import annotations

import logging
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


def _split_table_column(value: Any) -> tuple[str, list[str]]:
    """Split a ``table.column`` reference into table name and column list."""
    text = str(value)
    if "." in text:
        table, column = text.split(".", 1)
        return table, [column]
    return text, []


def _clean_relationship(rel: dict[str, Any]) -> dict[str, Any]:
    """Normalise a relationship so DoSI's parser accepts it.

    Legacy project files sometimes write ``type: one_to_many`` and put the
    dimension on the ``from`` side. DoSI expects ``from`` to be the many-side
    dataset (fact) and ``to`` the one-side dataset (dimension). When an explicit
    ``one_to_many`` type is present we flip the direction. For DoSI-native files
    without ``type`` we keep the direction as-is.
    """
    cleaned = {k: v for k, v in rel.items() if k in _SUPPORTED_REL_FIELDS}
    rel_type = str(rel.get("type", "")).lower()

    raw_from = cleaned.get("from")
    raw_to = cleaned.get("to")

    # Legacy one_to_many: from=one side (dim), to=many side (fact).
    # DoSI convention: from=many side, to=one side.
    if raw_from is not None and raw_to is not None and rel_type == "one_to_many":
        raw_from, raw_to = raw_to, raw_from

    if raw_from is not None:
        table, cols = _split_table_column(raw_from)
        cleaned["from"] = table
        if "from_columns" not in cleaned:
            cleaned["from_columns"] = cols
    if raw_to is not None:
        table, cols = _split_table_column(raw_to)
        cleaned["to"] = table
        if "to_columns" not in cleaned:
            cleaned["to_columns"] = cols

    if "name" not in cleaned:
        from_part = cleaned.get("from", "?")
        from_col = cleaned.get("from_columns", ["?"])[0]
        to_part = cleaned.get("to", "?")
        cleaned["name"] = f"{from_part}.{from_col} -> {to_part}"

    return cleaned


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return the top-level mapping."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _default_ossie_dir() -> Path:
    """Return the directory where ``*.ossie.yml`` files live."""
    return Path(BASE_DIR) / "ossie"


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

    # Clean relationships in every model.
    for model in models:
        if isinstance(model.get("relationships"), list):
            model["relationships"] = [
                _clean_relationship(rel)
                for rel in model["relationships"]
                if isinstance(rel, dict)
            ]

    # DoSI expects exactly one semantic_model per document, so merge all project
    # models into a single entry while preserving dataset/relationship/metric names.
    combined_model: dict[str, Any] = {
        "name": "smart_data_agent",
        "description": "Combined semantic model for Smart Data Agent.",
    }
    if models:
        first = models[0]
        combined_model["name"] = first.get("name", "smart_data_agent")
        if "description" in first:
            combined_model["description"] = first["description"]
        if "ai_context" in first:
            combined_model["ai_context"] = first["ai_context"]

    for key in ("datasets", "relationships", "metrics"):
        values: list[Any] = []
        for model in models:
            values.extend(model.get(key, []))
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
