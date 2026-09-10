"""FastAPI application factory.

Wires routers, CORS, startup hooks, and graceful shutdown.
Run via::

    uvicorn app.main:app --reload
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.graph import get_compiled_graph
from app.api import api_router
from app.config import CheckpointerKind, get_settings
from app.memory.checkpointer import build_checkpointer, shutdown_checkpointer
from app.memory.review_store import init_review_store
from app.memory.summary_store import init_summary_store
from app.tasks.summarizer import start_summarizer_task
from app.tools.mcp_loader import init_mcp_tools, shutdown_mcp

logger = logging.getLogger(__name__)


def _settings_attr(settings, name: str, default):
    """Safely read an attribute with a fallback default."""
    return getattr(settings, name, default)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm LangGraph + MCP at startup; tear down on shutdown."""
    settings = get_settings()
    logger.info(
        "Starting %s (provider=%s, checkpointer=%s, hitl=%s)",
        _settings_attr(settings, "app_name", "smart-data-agent"),
        settings.llm_provider.value,
        settings.checkpointer.value,
        settings.hitl,
    )

    # Build the async checkpointer up-front so the compiled graph can use it.
    checkpointer = await build_checkpointer(settings)

    # MCP is best-effort: any failure is logged and we fall back to built-in tools.
    loaded_mcp_tools: list[Any] = []
    try:
        loaded_mcp_tools = await init_mcp_tools()
        if loaded_mcp_tools:
            logger.info("Loaded %d MCP tool(s)", len(loaded_mcp_tools))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("MCP initialisation failed during startup: %s", exc)

    # Defensive: the graph may have been warmed during module import (before MCP
    # tools were loaded). Clear the cache so the server process always compiles
    # with the freshly-loaded toolset.
    get_compiled_graph.cache_clear()
    get_compiled_graph()

    # Initialize optional summary store for SQLite-backed deployments.
    await init_summary_store()

    # Initialize optional review store for SQLite-backed deployments.
    await init_review_store()

    # Start background task that periodically summarizes stale conversations.
    summarizer_task: asyncio.Task[None] | None = None
    if settings.checkpointer == CheckpointerKind.SQLITE:
        summarizer_task = await start_summarizer_task()

    try:
        yield
    finally:
        if summarizer_task is not None:
            summarizer_task.cancel()
            try:
                await summarizer_task
            except asyncio.CancelledError:
                pass
        await shutdown_mcp()
        await shutdown_checkpointer()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=_settings_attr(settings, "app_name", "Smart Data Agent"),
        version=_settings_attr(settings, "app_version", "0.1.0"),
        description=(
            "LangGraph-powered agent HTTP/WebSocket API. Supports multi-turn chat, "
            "tool calling, streaming responses, persistent memory, and human-in-the-loop."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings_attr(settings, "cors_allowed_origins", ["*"]),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    @app.get("/health", tags=["health"])
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "llm_provider": settings.llm_provider.value,
                "checkpointer": settings.checkpointer.value,
                "hitl": settings.hitl,
            }
        )

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": _settings_attr(settings, "app_name", "Smart Data Agent"),
                "version": _settings_attr(settings, "app_version", "0.1.0"),
                "docs": "/docs",
                "openapi": "/openapi.json",
                "health": "/health",
            }
        )

    return app


app = create_app()
