"""Local knowledge-base tool.

Retrieval backend is configurable:

- ``vector`` (default): embed each markdown section with a local sentence-transformers
  model and query via ChromaDB.
- ``token``: legacy token-overlap fallback.

Drop files into ``backend/data/knowledge/``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from langchain_core.tools import tool

from app.config import get_settings

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge"

# Matches Markdown ATX headers (# to ######) so we can split files into sections.
_HEADER_PATTERN = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


def _split_markdown_sections(content: str) -> list[tuple[str, str]]:
    """Split markdown content into (header, body) sections by headers.

    A plain-text file has a single empty-header section. Markdown files are
    split on lines beginning with 1-6 ``#`` characters so that retrieval can
    return the most relevant section rather than the start of the whole file.
    """
    if not content.strip():
        return []

    matches = list(_HEADER_PATTERN.finditer(content))
    if not matches:
        return [("", content.strip())]

    sections: list[tuple[str, str]] = []
    first_start = matches[0].start()
    if first_start > 0:
        intro = content[:first_start].strip()
        if intro:
            sections.append(("", intro))

    for i, match in enumerate(matches):
        header = match.group(0).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        sections.append((header, body))

    return sections


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z0-9_一-鿿]+", text.lower()) if len(t) > 1}


def _score(query_tokens: set[str], content: str) -> int:
    content_tokens = _tokenize(content)
    if not query_tokens or not content_tokens:
        return 0
    return len(query_tokens & content_tokens)


def _format_results(
    results: list[tuple[str, str, int | float, str]],
    snippet_max_chars: int,
    total_max_chars: int,
) -> str:
    """Format ranked ``(source, header, score, text)`` tuples into the legacy text.

    Each item is truncated to ``snippet_max_chars``; overall output is capped at
    ``total_max_chars``.
    """
    if not results:
        return "No relevant matches in the local knowledge base."

    blocks: list[str] = []
    total_chars = 0
    for source, header, score, text in results:
        snippet = text.strip()
        if len(snippet) > snippet_max_chars:
            snippet = snippet[:snippet_max_chars] + "..."
        if total_chars + len(snippet) > total_max_chars and blocks:
            break
        header_tag = f" / {header}" if header else ""
        blocks.append(f"File: {source}{header_tag} (score={score})\n{snippet}")
        total_chars += len(snippet)

    return "\n\n---\n\n".join(blocks) if blocks else "No relevant matches in the local knowledge base."


def _query_vector(query: str, top_k: int, filename_filter: str | None) -> str | None:
    """Try vector retrieval. Return None if unavailable or no results."""
    # Import lazily so that importing this tool at module-load time does not
    # drag in the heavy / circular dependency chain behind SemanticIndex.
    from app.services.semantic_index import _collection_name_from_file, get_semantic_index

    try:
        index = get_semantic_index()
    except Exception as exc:
        logger.warning("Vector index unavailable, falling back to token search: %s", exc)
        return None

    settings = get_settings()
    if filename_filter:
        collection_name = _collection_name_from_file(filename_filter)
        results = index.search(collection_name, query, top_k)
    else:
        results = index.search_all(query, top_k)

    if not results:
        return None

    tuples = [(r.source, r.header, r.score, r.text) for r in results]
    return _format_results(
        tuples,
        snippet_max_chars=settings.knowledge_snippet_max_chars,
        total_max_chars=settings.knowledge_max_chars,
    )


def _query_token(query: str, top_k: int, filename_filter: str | None) -> str:
    if not KNOWLEDGE_DIR.exists():
        return f"No knowledge base directory at {KNOWLEDGE_DIR}."

    files = [
        p
        for p in KNOWLEDGE_DIR.glob("**/*")
        if p.is_file() and p.suffix.lower() in {".md", ".txt"}
    ]
    if filename_filter:
        files = [p for p in files if p.name == filename_filter]
    if not files:
        return f"Knowledge base is empty. Add .md/.txt files under {KNOWLEDGE_DIR}."

    query_tokens = _tokenize(query)
    # Score each section independently so the most relevant paragraph is returned,
    # not just the beginning of each file.
    scored: list[tuple[int, Path, str, str]] = []  # (score, path, header, body)
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            continue
        is_markdown = path.suffix.lower() == ".md"
        sections = _split_markdown_sections(content) if is_markdown else [("", content.strip())]
        for header, body in sections:
            section_text = f"{header}\n{body}" if header else body
            score = _score(query_tokens, section_text)
            if score > 0:
                scored.append((score, path, header, body))

    if not scored:
        return "No relevant matches in the local knowledge base."

    scored.sort(key=lambda x: x[0], reverse=True)

    # Legacy per-snippet cap and budget based on top_k.
    max_snippet_chars = 1200
    budget = max(1, top_k) * max_snippet_chars
    results: list[tuple[str, str, int | float, str]] = []
    total_chars = 0
    for score, path, header, body in scored:
        section_text = f"{header}\n{body}" if header else body
        snippet = section_text.strip()
        if len(snippet) > max_snippet_chars:
            snippet = snippet[:max_snippet_chars] + "..."
        if total_chars + len(snippet) > budget:
            break
        results.append((path.name, header, score, snippet))
        total_chars += len(snippet)

    return _format_results(
        results,
        snippet_max_chars=max_snippet_chars,
        # Keep the legacy behavior: the formatter already applied the budget,
        # so set total high enough not to re-truncate.
        total_max_chars=budget,
    )


def _query(query: str, top_k: int, filename_filter: str | None = None) -> str:
    settings = get_settings()
    if settings.knowledge_search_mode == "vector":
        vector_answer = _query_vector(query, top_k, filename_filter)
        if vector_answer is not None:
            return vector_answer
        logger.info("Vector search produced no results, falling back to token search")

    return _query_token(query, top_k, filename_filter)


@tool
def knowledge_search(query: str, top_k: int = 3, filename_filter: str = "") -> str:
    """Search the local knowledge base (markdown / text files) for relevant passages.

    Args:
        query: natural-language question or keywords.
        top_k: number of top passages to return (1-5).
        filename_filter: optional exact filename to search within, e.g.
            "bird-semantic-layer-debit_card.md". Empty means search all files.
    """
    return _query(query, top_k, filename_filter=filename_filter or None)
