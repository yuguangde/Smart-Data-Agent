"""Checkpointer factory: in-memory for dev, sqlite (async) for persistence."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import CheckpointerKind, Settings

logger = logging.getLogger(__name__)

_built_checkpointer: BaseCheckpointSaver | None = None
_sqlite_conn: aiosqlite.Connection | None = None


async def build_checkpointer(settings: Settings) -> BaseCheckpointSaver:
    """Return a checkpointer matching the configured kind.

    The saver is built once and cached; subsequent calls return the same instance.
    For SQLite this uses ``AsyncSqliteSaver`` because the graph is driven through
    async APIs (``ainvoke`` / ``astream_events``).
    """
    global _built_checkpointer, _sqlite_conn
    if _built_checkpointer is not None:
        return _built_checkpointer

    if settings.checkpointer == CheckpointerKind.SQLITE:
        path = settings.sqlite_path_resolved
        logger.info("Using AsyncSqliteSaver at %s", path)
        _sqlite_conn = aiosqlite.connect(str(path))
        _built_checkpointer = AsyncSqliteSaver(_sqlite_conn)
        return _built_checkpointer

    logger.info("Using InMemorySaver (development mode)")
    _built_checkpointer = InMemorySaver()
    return _built_checkpointer


def get_checkpointer() -> BaseCheckpointSaver:
    """Return the previously built checkpointer or a fallback InMemorySaver."""
    if _built_checkpointer is None:
        logger.warning("No checkpointer built yet; using InMemorySaver fallback")
        return InMemorySaver()
    return _built_checkpointer


@asynccontextmanager
async def reset_checkpointer() -> AsyncIterator[None]:
    """Reset the cached checkpointer (useful before re-compiling the graph)."""
    global _built_checkpointer, _sqlite_conn
    try:
        yield
    finally:
        cp = _built_checkpointer
        conn = _sqlite_conn
        _built_checkpointer = None
        _sqlite_conn = None
        if conn is not None:
            try:
                await conn.close()
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("Error closing SQLite connection: %s", exc)
        elif cp is not None:
            close = getattr(cp, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning("Error closing checkpointer: %s", exc)


async def shutdown_checkpointer() -> None:
    """Tear down the checkpointer (e.g. close SQLite connections) on shutdown."""
    async with reset_checkpointer():
        pass


__all__ = ["build_checkpointer", "get_checkpointer", "reset_checkpointer", "shutdown_checkpointer"]
