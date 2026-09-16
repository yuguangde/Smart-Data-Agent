"""Local knowledge-base tool. Substring matching over .md/.txt files.

Designed as a deliberately simple baseline you can replace with a vector store later
(Chroma, Milvus, FAISS, etc.). Drop files into ``backend/data/knowledge/``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge"

# Matches Markdown ATX headers (# to ######) so we can split files into sections.
_HEADER_PATTERN = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z0-9_一-鿿]+", text.lower()) if len(t) > 1}


def _score(query_tokens: set[str], content: str) -> int:
    content_tokens = _tokenize(content)
    if not query_tokens or not content_tokens:
        return 0
    return len(query_tokens & content_tokens)


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
    # Treat any text before the first header as an intro section.
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


_MAX_SNIPPET_CHARS = 1200


def _query(query: str, top_k: int, filename_filter: str | None = None) -> str:
    if not KNOWLEDGE_DIR.exists():
        return f"No knowledge base directory at {KNOWLEDGE_DIR}."

    files = [p for p in KNOWLEDGE_DIR.glob("**/*") if p.is_file() and p.suffix.lower() in {".md", ".txt"}]
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

    blocks = []
    total_chars = 0
    # Allow roughly one medium-sized snippet per requested result.
    budget = max(1, top_k) * _MAX_SNIPPET_CHARS
    for score, path, header, body in scored:
        section_text = f"{header}\n{body}" if header else body
        snippet = section_text.strip()
        if len(snippet) > _MAX_SNIPPET_CHARS:
            snippet = snippet[:_MAX_SNIPPET_CHARS] + "..."
        if total_chars + len(snippet) > budget:
            break
        header_tag = f" / {header}" if header else ""
        blocks.append(f"File: {path.name}{header_tag} (score={score})\n{snippet}")
        total_chars += len(snippet)

    return "\n\n---\n\n".join(blocks)


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


__all__ = ["knowledge_search"]
