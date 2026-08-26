"""Checkpointer factory: in-memory for dev, sqlite for persistence."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import CheckpointerKind, Settings

logger = logging.getLogger(__name__)


def build_checkpointer(settings: Settings) -> InMemorySaver | SqliteSaver:
    """Return a checkpointer matching the configured kind."""
    if settings.checkpointer == CheckpointerKind.SQLITE:
        path = settings.sqlite_path_resolved
        logger.info("Using SqliteSaver at %s", path)
        # Sync-mode saver; use from_conn_string, then context-manager for proper teardown.
        ctx = SqliteSaver.from_conn_string(str(path))
        # The returned object is actually a ContextManager; use it directly as the saver.
        return ctx.__enter__()  # noqa: PLC2801 - intentional lifetime extension
    logger.info("Using InMemorySaver (development mode)")
    return InMemorySaver()


@contextmanager
def shutdown_checkpointer() -> Iterator[None]:
    """Tear down the checkpointer (e.g. close SQLite connections)."""
    from app.agent.graph import get_compiled_graph

    try:
        yield
    finally:
        # SqliteSaver in 0.2 supports `__exit__` for graceful close.
        cp = get_compiled_graph().checkpointer
        close = getattr(cp, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("Error closing checkpointer: %s", exc)


__all__ = ["build_checkpointer", "shutdown_checkpointer"]
