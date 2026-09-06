"""Shared helpers for the query DSL modules."""
from __future__ import annotations


def _extract_json(raw: str) -> str:
    """Extract JSON from the model output, tolerating markdown code fences.

    Some models add explanatory text before the fenced JSON block. We first
    look for a ``json`` labelled block, then any fenced block, and fall back
    to the stripped raw text.
    """
    cleaned = raw.strip()

    # 1. Prefer an explicitly labelled json fence anywhere in the text.
    start = cleaned.find("```json")
    if start != -1:
        block = cleaned[start:]
        end = block.find("```", len("```json"))
        if end != -1:
            return block[len("```json"):end].strip()

    # 2. Otherwise take the first generic fenced block.
    start = cleaned.find("```")
    if start != -1:
        block = cleaned[start:]
        end = block.find("```", 3)
        if end != -1:
            return block[3:end].strip()

    return cleaned
