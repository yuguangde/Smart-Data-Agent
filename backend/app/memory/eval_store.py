"""Persistent store for evaluation runs and per-case results.

Mirrors the ReviewStore pattern: lives in the same SQLite file as the
review store when SQLite persistence is enabled.
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
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id        TEXT PRIMARY KEY,
    dataset       TEXT NOT NULL,
    dataset_path  TEXT NOT NULL,
    status        TEXT NOT NULL,
    total         INTEGER NOT NULL DEFAULT 0,
    processed     INTEGER NOT NULL DEFAULT 0,
    passed        INTEGER NOT NULL DEFAULT 0,
    errored       INTEGER NOT NULL DEFAULT 0,
    metrics       TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_created ON eval_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_eval_runs_status ON eval_runs(status);

CREATE TABLE IF NOT EXISTS eval_results (
    result_id  TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    case_index INTEGER NOT NULL,
    question   TEXT NOT NULL,
    passed     INTEGER NOT NULL DEFAULT 0,
    scores     TEXT NOT NULL,
    answer     TEXT,
    error      TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_results_run_id ON eval_results(run_id);
"""

_store: "EvalStore | None" = None


class EvalStore:
    """Small aiosqlite wrapper for evaluation runs and results."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def ensure_schema(self) -> None:
        """Create evaluation tables if they do not exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_CREATE_TABLE_SQL)
            await db.commit()

    async def create_run(
        self,
        *,
        run_id: str,
        dataset: str,
        dataset_path: str,
        total: int,
    ) -> None:
        """Create a pending eval run row."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO eval_runs "
                "(run_id, dataset, dataset_path, status, total, processed, passed, errored, "
                "metrics, error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    dataset,
                    dataset_path,
                    "running",
                    total,
                    0,
                    0,
                    0,
                    json.dumps({}),
                    None,
                    now,
                    now,
                ),
            )
            await db.commit()

    async def update_run_progress(
        self,
        *,
        run_id: str,
        processed: int,
        passed: int,
        errored: int,
        metrics: dict[str, Any],
    ) -> None:
        """Update aggregate counters for a running eval run."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE eval_runs SET processed = ?, passed = ?, errored = ?, "
                "metrics = ?, updated_at = ? WHERE run_id = ?",
                (
                    processed,
                    passed,
                    errored,
                    json.dumps(metrics, ensure_ascii=False, default=str),
                    now,
                    run_id,
                ),
            )
            await db.commit()

    async def finalize_run(
        self,
        *,
        run_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        """Mark an eval run as completed or failed."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE eval_runs SET status = ?, error = ?, updated_at = ? WHERE run_id = ?",
                (status, error, now, run_id),
            )
            await db.commit()

    async def save_result(
        self,
        *,
        run_id: str,
        case_index: int,
        question: str,
        passed: bool,
        scores: dict[str, Any],
        answer: str,
        error: str | None = None,
    ) -> None:
        """Persist a single case result."""
        result_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO eval_results "
                "(result_id, run_id, case_index, question, passed, scores, answer, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    run_id,
                    case_index,
                    question,
                    1 if passed else 0,
                    json.dumps(scores, ensure_ascii=False, default=str),
                    answer,
                    error,
                    now,
                ),
            )
            await db.commit()

    async def list_runs(self, dataset: str | None = None) -> list[dict[str, Any]]:
        """Return evaluation runs, newest first.

        Args:
            dataset: If provided, only return runs for that dataset name.
        """
        async with aiosqlite.connect(self._db_path) as db:
            if dataset:
                async with db.execute(
                    "SELECT run_id, dataset, dataset_path, status, total, processed, passed, "
                    "errored, metrics, error, created_at, updated_at FROM eval_runs "
                    "WHERE dataset = ? ORDER BY created_at DESC",
                    (dataset,),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute(
                    "SELECT run_id, dataset, dataset_path, status, total, processed, passed, "
                    "errored, metrics, error, created_at, updated_at FROM eval_runs "
                    "ORDER BY created_at DESC"
                ) as cursor:
                    rows = await cursor.fetchall()
        return [
            {
                "run_id": row[0],
                "dataset": row[1],
                "dataset_path": row[2],
                "status": row[3],
                "total": row[4],
                "processed": row[5],
                "passed": row[6],
                "errored": row[7],
                "metrics": json.loads(row[8] or "{}"),
                "error": row[9],
                "created_at": row[10],
                "updated_at": row[11],
            }
            for row in rows
        ]

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return a single run by id."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT run_id, dataset, dataset_path, status, total, processed, passed, "
                "errored, metrics, error, created_at, updated_at FROM eval_runs WHERE run_id = ?",
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "dataset": row[1],
            "dataset_path": row[2],
            "status": row[3],
            "total": row[4],
            "processed": row[5],
            "passed": row[6],
            "errored": row[7],
            "metrics": json.loads(row[8] or "{}"),
            "error": row[9],
            "created_at": row[10],
            "updated_at": row[11],
        }

    async def get_run_results(self, run_id: str) -> list[dict[str, Any]]:
        """Return all case results for a run, ordered by case_index."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT result_id, run_id, case_index, question, passed, scores, answer, error, "
                "created_at FROM eval_results WHERE run_id = ? ORDER BY case_index",
                (run_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "result_id": row[0],
                "run_id": row[1],
                "case_index": row[2],
                "question": row[3],
                "passed": bool(row[4]),
                "scores": json.loads(row[5] or "{}"),
                "answer": row[6],
                "error": row[7],
                "created_at": row[8],
            }
            for row in rows
        ]


async def init_eval_store() -> EvalStore | None:
    """Create the process-wide eval store if SQLite persistence is in use."""
    global _store
    settings = get_settings()
    if settings.checkpointer != CheckpointerKind.SQLITE:
        logger.info("EvalStore disabled: checkpointer is not sqlite")
        _store = None
        return None

    path = str(settings.sqlite_path_resolved)
    store = EvalStore(path)
    await store.ensure_schema()
    _store = store
    logger.info("EvalStore initialized at %s", path)
    return store


def get_eval_store() -> EvalStore | None:
    """Return the initialized eval store, or None if persistence is off."""
    return _store


__all__ = ["EvalStore", "init_eval_store", "get_eval_store"]
