"""Persistent store for thread-level conversation summaries.

Works against the same SQLite file used by ``AsyncSqliteSaver`` if the
configured checkpointer is SQLite; otherwise it remains disabled.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.config import CheckpointerKind, get_settings

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS thread_summaries (
    thread_id          TEXT PRIMARY KEY,
    summary            TEXT NOT NULL,
    summarized_up_to   TEXT NOT NULL,
    generated_at       TEXT NOT NULL,
    model              TEXT
);
"""


class SummaryStore:
    """Small aiosqlite wrapper for saving/loading per-thread summaries."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        """Create the summary table if it does not exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE_SQL)
            await db.commit()

    async def get_summary(self, thread_id: str) -> dict[str, Any] | None:
        """Return the latest summary row for a thread, or None."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT summary, summarized_up_to, generated_at, model "
                "FROM thread_summaries WHERE thread_id = ?",
                (thread_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return {
                    "thread_id": thread_id,
                    "summary": row[0],
                    "summarized_up_to": row[1],
                    "generated_at": row[2],
                    "model": row[3],
                }

    async def save_summary(
        self,
        thread_id: str,
        summary: str,
        summarized_up_to: str,
        model: str | None = None,
    ) -> None:
        """Insert or replace a summary row."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO thread_summaries "
                "(thread_id, summary, summarized_up_to, generated_at, model) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET "
                "summary=excluded.summary, "
                "summarized_up_to=excluded.summarized_up_to, "
                "generated_at=excluded.generated_at, "
                "model=excluded.model",
                (thread_id, summary, summarized_up_to, now, model),
            )
            await db.commit()


_store: SummaryStore | None = None


async def init_summary_store() -> SummaryStore | None:
    """Create the process-wide summary store if SQLite persistence is in use."""
    global _store
    settings = get_settings()
    if settings.checkpointer != CheckpointerKind.SQLITE:
        logger.info("SummaryStore disabled: checkpointer is not sqlite")
        _store = None
        return None

    path = str(settings.sqlite_path_resolved)
    store = SummaryStore(path)
    await store.ensure_schema()
    _store = store
    logger.info("SummaryStore initialized at %s", path)
    return store


def get_summary_store() -> SummaryStore | None:
    """Return the initialized summary store, or None if persistence is off."""
    return _store
