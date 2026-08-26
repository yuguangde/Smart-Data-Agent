"""Web search tool. Uses duckduckgo-search when available, else no-op stub."""
from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _do_search(query: str, max_results: int) -> str:
    """Try DuckDuckGo; gracefully degrade if library missing or network fails."""
    try:
        from duckduckgo_search import DDGS  # type: ignore[import-untyped]
    except ImportError:
        return (
            "Web search is unavailable (duckduckgo-search not installed). "
            "Answer from general knowledge or ask the user to provide sources."
        )

    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=max_results))
    except Exception as exc:  # network / rate-limit / etc.
        logger.warning("DuckDuckGo search failed: %s", exc)
        return f"Web search failed: {exc}"

    if not hits:
        return "No results."

    lines = []
    for i, hit in enumerate(hits, 1):
        title = hit.get("title") or "(no title)"
        href = hit.get("href") or ""
        snippet = hit.get("body") or ""
        lines.append(f"[{i}] {title}\n    URL: {href}\n    {snippet}")
    return "\n\n".join(lines)


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the public web for `query` and return up to `max_results` results.

    Each result is formatted as "[n] title \\n URL \\n snippet". Use when up-to-date info
    is needed and the local knowledge base is insufficient.
    """
    max_results = max(1, min(int(max_results), 10))
    return _do_search(query, max_results)


__all__ = ["web_search"]
