"""FastAPI application factory.

Wires routers, CORS, startup hooks, and graceful shutdown.
Run via::

    uvicorn app.main:app --reload
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.api.chat import health as chat_health
from app.api.schemas import HealthResponse
from app.bootstrap import bootstrap_agent, shutdown_agent
from app.config import CheckpointerKind, get_settings
from app.memory.eval_store import init_eval_store
from app.memory.review_store import init_review_store
from app.memory.summary_store import init_summary_store
from app.tasks.summarizer import start_summarizer_task

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

    # Bootstrap the agent: checkpointer, MCP tools (DoSI/StarRocks), and compile
    # the graph with the freshly-loaded toolset.
    await bootstrap_agent()

    # Initialize optional summary store for SQLite-backed deployments.
    await init_summary_store()

    # Initialize optional review store for SQLite-backed deployments.
    await init_review_store()

    # Initialize optional evaluation store for SQLite-backed deployments.
    await init_eval_store()

    # Warm the vector-backed semantic index.  Heavy work (embedding + Chroma
    # writes) is delegated to a worker thread so startup stays responsive.
    semantic_index = None
    if settings.vector_store_enabled:
        # Import lazily here to avoid a circular import at module-load time:
        # app.agent.graph -> app.tools -> app.services -> app.agent.graph.
        from app.services.semantic_index import KNOWLEDGE_DIR, get_semantic_index
        try:
            semantic_index = get_semantic_index()
            await asyncio.to_thread(
                semantic_index.ensure_indexed_knowledge_dir, KNOWLEDGE_DIR
            )
            logger.info(
                "Vector semantic index ready (%s)",
                settings.embedding_model,
            )
        except Exception as exc:
            logger.warning("Vector semantic index warmup failed: %s", exc)

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
        if semantic_index is not None:
            try:
                semantic_index.close()
            except Exception:
                pass
        await shutdown_agent()


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

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        """Root-level health probe; delegates to the canonical /api/health implementation."""
        return await chat_health()

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
