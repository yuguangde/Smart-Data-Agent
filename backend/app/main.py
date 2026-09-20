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
from app.api.chat import health as chat_health
from app.api.schemas import HealthResponse
from app.config import CheckpointerKind, get_settings
from app.memory.checkpointer import build_checkpointer, shutdown_checkpointer
from app.memory.eval_store import init_eval_store
from app.memory.review_store import init_review_store
from app.memory.summary_store import init_summary_store
from app.semantic.dosi_model import build_dosi_model
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

    # Prepare the combined DoSI model file when DoSI MCP is enabled.
    dosi_proc: asyncio.subprocess.Process | None = None
    if settings.dosi_mcp_enabled:
        try:
            build_dosi_model()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to build DoSI model file: %s", exc)

        if settings.dosi_start_server:
            dosi_proc = await _start_dosi_server(settings)
            if dosi_proc is not None:
                # Give dosi-server a moment to bind before MCP connect.
                await asyncio.sleep(0.5)

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
        if dosi_proc is not None:
            await _stop_dosi_server(dosi_proc)
        await shutdown_mcp()
        await shutdown_checkpointer()


async def _start_dosi_server(
    settings: Any,
) -> asyncio.subprocess.Process | None:
    """Start the DoSI MCP server as a local subprocess.

    Returns the subprocess handle, or ``None`` if the binary is missing.
    """
    bin_path = str(_settings_attr(settings, "dosi_server_bin", "dosi-server"))
    try:
        import shutil

        if not shutil.which(bin_path):
            logger.warning(
                "DoSI server binary not found on PATH: %s. "
                "Set DOSI_SERVER_BIN or install DoSI.",
                bin_path,
            )
            return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not check for DoSI server binary: %s", exc)
        return None

    bind = f"{_settings_attr(settings, 'dosi_server_listen_host', '127.0.0.1')}:" \
           f"{_settings_attr(settings, 'dosi_server_listen_port', 8080)}"
    model_path = str(_settings_attr(settings, "dosi_model_path", "./data/dosi-model.yaml"))
    transport = _settings_attr(settings, "dosi_mcp_transport", "streamable_http")
    transport_value = (
        transport.value
        if hasattr(transport, "value")
        else str(transport)
    )

    # dosi-server uses --bind for HTTP and --mcp-stdio for stdio MCP.
    cmd = [
        bin_path,
        "--model", model_path,
        "--bind", bind,
    ]
    if transport_value == "stdio":
        cmd.append("--mcp-stdio")
    logger.info("Starting DoSI server: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return proc
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to start DoSI server: %s", exc)
        return None


async def _stop_dosi_server(proc: asyncio.subprocess.Process) -> None:
    """Gracefully terminate the embedded DoSI server subprocess."""
    try:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("DoSI server did not exit gracefully; killing it")
                proc.kill()
                await proc.wait()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Error stopping DoSI server: %s", exc)


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
