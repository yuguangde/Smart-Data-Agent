"""User intent classification for data questions.

A simple keyword-based router that decides whether a question should be
answered via the controlled metric-DSL pipeline or via free-form nl2sql.
"""
from __future__ import annotations

import re
from enum import StrEnum

from app.query.registry import SemanticRegistry


class UserIntent(StrEnum):
    """High-level intent for a data question."""

    METRIC_ANALYSIS = "metric_analysis"
    FREE_EXPLORATION = "free_exploration"


def _normalize(text: str) -> str:
    """Normalize text for keyword matching.

    Lower-cases the text and replaces runs of punctuation / non-word characters
    with a single space. Chinese characters are treated as word characters.
    """
    text = text.lower()
    text = re.sub(r"[^\w\s]+", " ", text)
    return " ".join(text.split())


def classify_question(
    question: str,
    registry: SemanticRegistry | None = None,
) -> UserIntent:
    """Classify ``question`` as metric analysis or free exploration.

    The current heuristic is intentionally simple: if any metric name defined
    in the semantic registry appears in the question, treat it as a metric
    analysis question. Otherwise fall back to free exploration.
    """
    registry = registry or SemanticRegistry.get()
    text = _normalize(question)

    for metric_name in registry.metrics:
        if _normalize(metric_name) in text:
            return UserIntent.METRIC_ANALYSIS

    return UserIntent.FREE_EXPLORATION


__all__ = ["UserIntent", "classify_question"]
