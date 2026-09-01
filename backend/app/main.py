"""FastAPI application factory.

Wires routers, CORS, startup hooks, and graceful shutdown.
Run via::

    uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.graph import get_compiled_graph
from app.api import api_router
from app.config import get_settings
from app.memory.checkpointer import shutdown_checkpointer
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

    # MCP is best-effort: any failure is logged and we fall back to built-in tools.
    try:
        tools = await init_mcp_tools()
        if tools:
            logger.info("Loaded %d MCP tool(s)", len(tools))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("MCP initialisation failed during startup: %s", exc)

    get_compiled_graph()

    try:
        yield
    finally:
        await shutdown_mcp()
        with shutdown_checkpointer():
            pass


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
