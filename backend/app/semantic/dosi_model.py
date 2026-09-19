"""Combine project OSSIE YAML files into a single DoSI model entrypoint.

DoSI expects one Apache Ossie model file on the command line. The project keeps
multiple ``*.ossie.yml`` files under ``backend/ossie/``. This module merges them
into one temporary/combined YAML at startup so DoSI can consume the full
semantic layer.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.config import BASE_DIR

logger = logging.getLogger(__name__)


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
) -> Path:
    """Merge all ``*.ossie.yml`` files into one DoSI model file.

    Args:
        ossie_dir: Directory to scan. Defaults to ``backend/ossie/``.
        output_path: Where to write the combined model. Defaults to the value
            configured by ``settings.dosi_model_path``.

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

    models: list[dict[str, Any]] = []
    for path in sorted(ossie_dir.glob("*.ossie.yml")):
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

    combined = {"semantic_model": models}
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
