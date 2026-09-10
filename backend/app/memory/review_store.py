"""Persistent store for review-agent results.

Lives in the same SQLite file as the checkpointer when SQLite persistence is
enabled; otherwise it remains disabled.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.config import CheckpointerKind, get_settings

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS thread_reviews (
    review_id          TEXT PRIMARY KEY,
    thread_id          TEXT NOT NULL,
    review_thread_id   TEXT,
    strategy           TEXT NOT NULL,
    main_report        TEXT NOT NULL,
    review_report      TEXT NOT NULL,
    comparison         TEXT NOT NULL,
    review_model       TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_thread_reviews_thread_id ON thread_reviews(thread_id);
"""

_MIGRATE_TABLE_SQL = """
ALTER TABLE thread_reviews ADD COLUMN review_thread_id TEXT;
"""

_store: "ReviewStore | None" = None


class ReviewStore:
    """Small aiosqlite wrapper for saving/loading review results per thread."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        """Create the review table if it does not exist and migrate old tables."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_CREATE_TABLE_SQL)
            try:
                await db.execute(_MIGRATE_TABLE_SQL)
            except Exception:
                # Column already exists or other non-fatal migration issue.
                pass
            await db.commit()

    async def save_review(
        self,
        *,
        thread_id: str,
        review_thread_id: str,
        strategy: str,
        main_report: str,
        review_report: str,
        comparison: dict[str, Any],
        review_model: str | None = None,
    ) -> str:
        """Persist a review result and return its review_id."""
        review_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO thread_reviews "
                "(review_id, thread_id, review_thread_id, strategy, main_report, review_report, comparison, review_model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    thread_id,
                    review_thread_id,
                    strategy,
                    main_report,
                    review_report,
                    json.dumps(comparison, ensure_ascii=False, default=str),
                    review_model,
                    now,
                ),
            )
            await db.commit()
        return review_id

    async def list_reviews(
        self, thread_id: str
    ) -> list[dict[str, Any]]:
        """Return review summaries for a thread, newest first."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT review_id, thread_id, review_thread_id, strategy, review_model, comparison, created_at "
                "FROM thread_reviews WHERE thread_id = ? ORDER BY created_at DESC",
                (thread_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                comparison = json.loads(row[5])
            except Exception:
                comparison = {}
            result.append(
                {
                    "review_id": row[0],
                    "thread_id": row[1],
                    "review_thread_id": row[2] or "",
                    "strategy": row[3],
                    "review_model": row[4] or "",
                    "verdict": comparison.get("verdict", "partial"),
                    "summary": comparison.get("summary", ""),
                    "created_at": row[6],
                }
            )
        return result

    async def get_review(self, review_id: str) -> dict[str, Any] | None:
        """Return a single review record by id."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT review_id, thread_id, review_thread_id, strategy, main_report, review_report, comparison, "
                "review_model, created_at FROM thread_reviews WHERE review_id = ?",
                (review_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return {
            "review_id": row[0],
            "thread_id": row[1],
            "review_thread_id": row[2] or "",
            "strategy": row[3],
            "main_report": row[4],
            "review_report": row[5],
            "comparison": json.loads(row[6]),
            "review_model": row[7],
            "created_at": row[8],
        }

    async def get_latest_review(self, thread_id: str) -> dict[str, Any] | None:
        """Return the most recent review record for a thread, or None."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT review_id, thread_id, review_thread_id, strategy, main_report, review_report, comparison, "
                "review_model, created_at FROM thread_reviews WHERE thread_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return {
            "review_id": row[0],
            "thread_id": row[1],
            "review_thread_id": row[2] or "",
            "strategy": row[3],
            "main_report": row[4],
            "review_report": row[5],
            "comparison": json.loads(row[6]),
            "review_model": row[7],
            "created_at": row[8],
        }


async def init_review_store() -> ReviewStore | None:
    """Create the process-wide review store if SQLite persistence is in use."""
    global _store
    settings = get_settings()
    if settings.checkpointer != CheckpointerKind.SQLITE:
        logger.info("ReviewStore disabled: checkpointer is not sqlite")
        _store = None
        return None

    path = str(settings.sqlite_path_resolved)
    store = ReviewStore(path)
    await store.ensure_schema()
    _store = store
    logger.info("ReviewStore initialized at %s", path)
    return store


def get_review_store() -> ReviewStore | None:
    """Return the initialized review store, or None if persistence is off."""
    return _store


__all__ = ["ReviewStore", "init_review_store", "get_review_store"]
