"""Process-level bootstrap helpers for the agent.

Used by the FastAPI lifespan, offline evaluation runners, and standalone
scripts to ensure the LangGraph agent is compiled with the same toolset
(built-in + MCP) that the running HTTP application sees.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from app.agent.graph import get_compiled_graph
from app.config import get_settings
from app.memory.checkpointer import build_checkpointer, shutdown_checkpointer
from app.semantic.dosi_model import build_dosi_model
from app.tools.mcp_loader import init_mcp_tools, shutdown_mcp

logger = logging.getLogger(__name__)

_dosi_proc: asyncio.subprocess.Process | None = None
_bootstrapped: bool = False


def _settings_attr(settings: Any, name: str, default: Any) -> Any:
    """Safely read a settings attribute with a fallback default."""
    return getattr(settings, name, default)


async def _start_dosi_server(settings: Any) -> asyncio.subprocess.Process | None:
    """Start the DoSI MCP server as a local subprocess.

    Returns the subprocess handle, or ``None`` if the binary is missing.
    """
    bin_path = str(_settings_attr(settings, "dosi_server_bin", "dosi-server"))
    try:
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

    bind = (
        f"{_settings_attr(settings, 'dosi_server_listen_host', '127.0.0.1')}:"
        f"{_settings_attr(settings, 'dosi_server_listen_port', 8080)}"
    )
    model_path = str(_settings_attr(settings, "dosi_model_path", "./data/dosi-model.yaml"))
    transport = _settings_attr(settings, "dosi_mcp_transport", "streamable_http")
    transport_value = transport.value if hasattr(transport, "value") else str(transport)

    cmd = [
        bin_path,
        "--model",
        model_path,
        "--bind",
        bind,
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


async def _stop_dosi_server(proc: asyncio.subprocess.Process | None) -> None:
    """Gracefully terminate the embedded DoSI server subprocess."""
    if proc is None:
        return
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


async def bootstrap_agent(*, enable_mcp: bool = True) -> None:
    """Bootstrap the agent once per process: checkpointer, MCP tools, graph.

    The function is idempotent; subsequent calls are no-ops unless
    ``shutdown_agent()`` has been called in between.
    """
    global _bootstrapped
    if _bootstrapped:
        return

    settings = get_settings()
    logger.info("Bootstrapping agent (enable_mcp=%s)", enable_mcp)

    # Build the async checkpointer up-front so the compiled graph can use it.
    await build_checkpointer(settings)

    if enable_mcp:
        # Prepare the combined DoSI model file when DoSI MCP is enabled.
        if settings.dosi_mcp_enabled:
            try:
                build_dosi_model()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to build DoSI model file: %s", exc)

            if settings.dosi_start_server:
                global _dosi_proc
                _dosi_proc = await _start_dosi_server(settings)
                if _dosi_proc is not None:
                    # Give dosi-server a moment to bind before MCP connect.
                    await asyncio.sleep(0.5)

        # MCP is best-effort: any failure is logged and we fall back to built-in tools.
        try:
            loaded_mcp_tools = await init_mcp_tools()
            if loaded_mcp_tools:
                logger.info("Loaded %d MCP tool(s)", len(loaded_mcp_tools))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("MCP initialisation failed during startup: %s", exc)

    # Defensive: the graph may have been warmed during module import (before MCP
    # tools were loaded). Clear the cache so the process always compiles with the
    # freshly-loaded toolset.
    get_compiled_graph.cache_clear()
    get_compiled_graph()

    _bootstrapped = True


async def ensure_agent_bootstrapped(*, enable_mcp: bool = True) -> None:
    """Convenience wrapper for service-layer entry points."""
    await bootstrap_agent(enable_mcp=enable_mcp)


async def shutdown_agent() -> None:
    """Tear down resources started by ``bootstrap_agent()``.

    Safe to call multiple times.
    """
    global _bootstrapped, _dosi_proc
    if _dosi_proc is not None:
        await _stop_dosi_server(_dosi_proc)
        _dosi_proc = None
    await shutdown_mcp()
    await shutdown_checkpointer()
    _bootstrapped = False


__all__ = ["bootstrap_agent", "ensure_agent_bootstrapped", "shutdown_agent"]
