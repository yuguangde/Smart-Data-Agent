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


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z0-9_一-鿿]+", text.lower()) if len(t) > 1}


def _score(query_tokens: set[str], content: str) -> int:
    content_tokens = _tokenize(content)
    if not query_tokens or not content_tokens:
        return 0
    return len(query_tokens & content_tokens)


def _query(query: str, top_k: int) -> str:
    if not KNOWLEDGE_DIR.exists():
        return f"No knowledge base directory at {KNOWLEDGE_DIR}."

    files = [p for p in KNOWLEDGE_DIR.glob("**/*") if p.is_file() and p.suffix.lower() in {".md", ".txt"}]
    if not files:
        return f"Knowledge base is empty. Add .md/.txt files under {KNOWLEDGE_DIR}."

    query_tokens = _tokenize(query)
    scored: list[tuple[int, Path, str]] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            continue
        score = _score(query_tokens, content)
        if score > 0:
            scored.append((score, path, content))

    if not scored:
        return "No relevant matches in the local knowledge base."

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: max(1, min(top_k, 5))]

    blocks = []
    for score, path, content in top:
        snippet = content.strip().replace("\n", " ")
        if len(snippet) > 400:
            snippet = snippet[:400] + "..."
        blocks.append(f"File: {path.name} (score={score})\n{snippet}")
    return "\n\n---\n\n".join(blocks)


@tool
def knowledge_search(query: str, top_k: int = 3) -> str:
    """Search the local knowledge base (markdown / text files) for relevant passages.

    Args:
        query: natural-language question or keywords.
        top_k: number of top passages to return (1-5).
    """
    return _query(query, top_k)


__all__ = ["knowledge_search"]
