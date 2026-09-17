"""Tests for the vector-backed semantic index.

These tests are skipped if the optional ``chromadb`` / ``sentence-transformers``
dependencies are not installed, so the test suite keeps running on minimal envs.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

skip_if_no_deps = pytest.mark.skipif(
    importlib.util.find_spec("chromadb") is None
    or importlib.util.find_spec("sentence_transformers") is None,
    reason="chromadb and sentence-transformers are not installed",
)


@pytest.fixture
def vector_settings(tmp_path):
    """Build a Settings instance that points vector storage at tmp_path."""
    from app.config import Settings, get_settings

    # Inherit the embedding model configured in .env (e.g. a ModelScope/HF
    # mirror path) so tests do not try to re-download from huggingface.co.
    base = get_settings()
    return Settings(
        vector_store_enabled=True,
        knowledge_search_mode="vector",
        embedding_model=base.embedding_model,
        embedding_device=base.embedding_device,
        vector_store_path=str(tmp_path / "chroma"),
        embedding_local_cache_dir=str(tmp_path / "models"),
        knowledge_max_chars=4000,
        knowledge_snippet_max_chars=1500,
    )


@pytest.fixture
def sample_kb(tmp_path):
    """Create a minimal markdown knowledge file."""
    md = tmp_path / "bird-semantic-layer-debit_card_specializing.md"
    md.write_text(
        "# BIRD debit_card_specializing\n\n"
        "## 1. 业务背景\n\n"
        "SME、LAM 和 KAM 是本数据库的客户 Segment。\n\n"
        "## 2. 表语义\n\n"
        "### customers\n\n"
        "CustomerID 是主键；Segment 可取 SME / LAM / KAM。\n\n"
        "### yearmonth\n\n"
        "Date 是 YYYYMM 纯文本，消费字段是 Consumption。\n\n"
        "## 4. 典型正确 SQL 模式\n\n"
        "### 模式 5：哪一年/月消费量最高/最低（只返回年份/月份）\n\n"
        "```sql\n"
        "SELECT SUBSTR(T2.Date, 5, 2)\n"
        "FROM customers AS T1\n"
        "INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID\n"
        "WHERE T1.Segment = 'SME'\n"
        "GROUP BY SUBSTR(T2.Date, 5, 2)\n"
        "ORDER BY SUM(T2.Consumption) DESC\n"
        "LIMIT 1;\n"
        "```\n",
        encoding="utf-8",
    )
    return md


@skip_if_no_deps
def test_collection_name_from_file():
    from app.services.semantic_index import _collection_name_from_file

    assert _collection_name_from_file("bird-semantic-layer-debit_card_specializing.md") == "debit_card_specializing"
    assert _collection_name_from_file("bird-semantic-layer-student_club.md") == "student_club"
    assert _collection_name_from_file("random_notes.md") == "general"


@skip_if_no_deps
def test_split_markdown_sections():
    from app.services.semantic_index import _split_markdown_sections

    sections = _split_markdown_sections("# A\nbody A\n## B\nbody B\n")
    assert len(sections) == 2
    assert sections[0][0] == "# A"
    assert "body A" in sections[0][1]
    assert sections[1][0] == "## B"


@skip_if_no_deps
def test_semantic_index_build_and_search(vector_settings, sample_kb):
    from app.services.semantic_index import SemanticIndex

    index = SemanticIndex(vector_settings)
    collection_name = index.ensure_indexed(sample_kb)
    assert collection_name == "debit_card_specializing"

    collection = index._client.get_collection(collection_name)
    assert collection.count() > 0

    query = (
        "What was the gas consumption peak month for SME customers in 2013?\n\n"
        "CREATE TABLE customers (...)\n"
        "CREATE TABLE yearmonth (Date TEXT, Consumption REAL)"
    )
    results = index.search(collection_name, query, top_k=5)
    assert results

    # The top result should be the "模式 5" section (or at least it should
    # appear in the top-5).
    text = "\n".join(r.text for r in results).lower()
    assert "sub" in text and "consumption" in text

    headers = [r.header for r in results if r.header]
    assert any("模式 5" in h for h in headers) or any("yearmonth" in h for h in headers)


@skip_if_no_deps
def test_semantic_index_rebuild_on_mtime_change(vector_settings, sample_kb):
    from app.services.semantic_index import SemanticIndex

    index = SemanticIndex(vector_settings)
    index.ensure_indexed(sample_kb)

    collection = index._client.get_collection("debit_card_specializing")
    first_count = collection.count()
    assert first_count > 0

    # Wait a tiny bit so mtime actually changes, then append content.
    time.sleep(0.05)
    sample_kb.write_text(
        sample_kb.read_text(encoding="utf-8")
        + "\n\n## 99. Extra section\n\nExtra body.\n",
        encoding="utf-8",
    )

    index.ensure_indexed(sample_kb)
    collection = index._client.get_collection("debit_card_specializing")
    assert collection.count() == first_count + 1
