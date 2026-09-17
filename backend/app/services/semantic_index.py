"""Vector-backed semantic index for the local knowledge base.

This module replaces the simple token-overlap retrieval in
``app.tools.knowledge_search`` with ChromaDB + a local sentence-transformers
embedding model.  Each markdown section becomes one document; BIRD semantic-layer
files are grouped into a collection per database so eval retrieval stays scoped.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.tools.knowledge_search import KNOWLEDGE_DIR, _split_markdown_sections

logger = logging.getLogger(__name__)


def _collection_name_from_file(filename: str) -> str:
    """Derive a Chroma collection name from a knowledge-base filename.

    ``bird-semantic-layer-debit_card_specializing.md`` maps to
    ``debit_card_specializing`` so that BIRD eval retrieval stays scoped to the
    current database.  Other files fall back to a single ``general`` collection.
    """
    prefix = "bird-semantic-layer-"
    if filename.startswith(prefix) and filename.endswith(".md"):
        return filename[len(prefix) : -3]
    return "general"


class EmbeddingModel:
    """Thin wrapper around ``sentence-transformers`` for local embeddings."""

    def __init__(self, model_name: str, device: str, cache_dir: Path) -> None:
        # Import lazily so that importing this module does not drag in torch
        # unless vector search is actually enabled.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(
            model_name_or_path=model_name,
            device=device,
            cache_folder=str(cache_dir),
            trust_remote_code=False,
        )

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return a list of normalized embedding vectors for the input texts."""
        if not texts:
            return []

        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return embeddings.tolist()


@dataclass
class SearchResult:
    rank: int
    score: float  # Chroma L2 distance; smaller is more similar
    source: str
    header: str
    text: str


class SemanticIndex:
    """Manages Chroma collections and local embeddings for the knowledge base."""

    def __init__(self, settings: Settings) -> None:
        import chromadb

        self._settings = settings
        self._embedding = EmbeddingModel(
            model_name=settings.embedding_model,
            device=settings.embedding_device,
            cache_dir=settings.embedding_cache_dir_resolved,
        )
        self._client = chromadb.PersistentClient(
            path=str(settings.vector_store_path_resolved),
            settings=chromadb.Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )
        self._lock = threading.Lock()

    def ensure_indexed(self, file_path: Path) -> str:
        """Index a single knowledge file into its Chroma collection.

        The collection is keyed by the source file path.  If the file has not
        changed since the last indexing run, this method is a cheap no-op.
        """
        file_path = file_path.resolve()
        filename = file_path.name
        collection_name = _collection_name_from_file(filename)
        source_mtime = file_path.stat().st_mtime

        with self._lock:
            try:
                collection = self._client.get_or_create_collection(name=collection_name)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to get/create collection %s: %s", collection_name, exc
                )
                raise

            meta = collection.metadata or {}
            if (
                meta.get("source_file") == str(file_path)
                and meta.get("file_mtime") == source_mtime
                and meta.get("embedding_model") == self._settings.embedding_model
            ):
                logger.debug("Collection %s is up to date", collection_name)
                return collection_name

            # Otherwise rebuild the collection for this file.
            try:
                self._client.delete_collection(name=collection_name)
            except Exception:
                pass

            collection = self._client.create_collection(
                name=collection_name,
                metadata={
                    "source_file": str(file_path),
                    "file_mtime": source_mtime,
                    "embedding_model": self._settings.embedding_model,
                },
            )

            content = file_path.read_text(encoding="utf-8", errors="ignore")
            sections = _split_markdown_sections(content)
            if not sections:
                logger.warning("No sections found in %s", file_path)
                return collection_name

            ids: list[str] = []
            documents: list[str] = []
            metadatas: list[dict[str, Any]] = []
            for idx, (header, body) in enumerate(sections):
                document = f"{header}\n{body}".strip() if header else body
                ids.append(f"{collection_name}-{idx}")
                documents.append(document)
                metadatas.append(
                    {
                        "source": filename,
                        "header": header,
                        "section_index": idx,
                    }
                )

            embeddings = self._embedding.encode(documents)
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
            logger.info(
                "Indexed %s: %d sections into collection %s",
                filename,
                len(sections),
                collection_name,
            )
            return collection_name

    def ensure_indexed_knowledge_dir(self, knowledge_dir: Path) -> list[str]:
        """Index every ``.md`` / ``.txt`` file under ``knowledge_dir``.

        Returns the list of collection names that were touched.
        """
        knowledge_dir = knowledge_dir.resolve()
        files = sorted(
            p
            for p in knowledge_dir.glob("**/*")
            if p.is_file() and p.suffix.lower() in {".md", ".txt"}
        )
        collections: list[str] = []
        for path in files:
            try:
                collections.append(self.ensure_indexed(path))
            except Exception as exc:
                logger.warning("Failed to index %s: %s", path, exc)
        return collections

    def _collection_names(self) -> list[str]:
        """Return user collection names (skip Chroma system collections)."""
        names = self._client.list_collections()
        # Chroma versions differ: list_collections may return strings or objects.
        if names and not isinstance(names[0], str):
            names = [c.name for c in names]
        return [n for n in names if not n.startswith("chroma_")]

    def _query_collection(
        self, collection_name: str, query_embedding: list[float], top_k: int
    ) -> list[SearchResult]:
        collection = self._client.get_or_create_collection(name=collection_name)
        if collection.count() == 0:
            return []

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        search_results: list[SearchResult] = []
        for rank, (doc_id, distance, document, metadata) in enumerate(
            zip(
                results["ids"][0],
                results["distances"][0],
                results["documents"][0],
                results["metadatas"][0],
            ),
            start=1,
        ):
            search_results.append(
                SearchResult(
                    rank=rank,
                    score=float(distance),
                    source=metadata.get("source", collection_name),
                    header=metadata.get("header", ""),
                    text=document,
                )
            )
        return search_results

    def search(self, collection_name: str, query: str, top_k: int) -> list[SearchResult]:
        """Search a single collection by semantic similarity."""
        query_embedding = self._embedding.encode([query])[0]
        return self._query_collection(collection_name, query_embedding, top_k)

    def search_all(self, query: str, top_k: int) -> list[SearchResult]:
        """Search across all collections and return the global top-k."""
        query_embedding = self._embedding.encode([query])[0]
        all_results: list[SearchResult] = []
        for collection_name in self._collection_names():
            try:
                all_results.extend(
                    self._query_collection(collection_name, query_embedding, top_k)
                )
            except Exception as exc:
                logger.warning("Search failed for collection %s: %s", collection_name, exc)

        all_results.sort(key=lambda r: r.score)
        return all_results[:top_k]

    def close(self) -> None:
        """Best-effort cleanup; persistent storage is flushed automatically."""
        # PersistentClient writes to disk immediately, so no explicit action is
        # required.  We intentionally do NOT call client.reset() here.
        pass


# Module-level singleton.  Intentionally lazy: importing this file does not load
# torch or Chroma until ``get_semantic_index()`` is called.
_semantic_index: SemanticIndex | None = None
_semantic_index_error: str | None = None
_semantic_index_lock = threading.Lock()


def get_semantic_index() -> SemanticIndex:
    """Return the shared ``SemanticIndex`` singleton."""
    global _semantic_index, _semantic_index_error

    if _semantic_index is not None:
        return _semantic_index

    with _semantic_index_lock:
        if _semantic_index is not None:
            return _semantic_index

        settings = get_settings()
        if not settings.vector_store_enabled or settings.knowledge_search_mode != "vector":
            raise RuntimeError(
                "Vector store is disabled (vector_store_enabled=false or "
                "knowledge_search_mode != 'vector')"
            )

        instance = SemanticIndex(settings)
        _semantic_index = instance
        _semantic_index_error = None
        return instance


def is_semantic_index_available() -> bool:
    """Return True if the vector index has been successfully initialized."""
    return _semantic_index is not None


def get_semantic_index_error() -> str | None:
    """Return the last initialization error message, if any."""
    return _semantic_index_error
