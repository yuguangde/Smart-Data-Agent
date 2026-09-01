"""Process-wide cache of MCP-sourced tools.

This module owns the *runtime* state for MCP-backed tools: it lazily
connects to the configured MCP server (typically a StarRocks MCP instance)
and exposes the resulting LangChain ``BaseTool`` list to the rest of the
application.

``app.tools.get_all_tools()`` calls :func:`list_tools` here to merge MCP
tools with the built-in toolset. Initialisation is meant to run during
FastAPI lifespan startup (see ``app.main.lifespan``) so the first chat
request does not pay the connect handshake.

Failure modes are deliberately non-fatal: if the MCP server is unreachable
or returns no tools, we log a warning and return an empty list. The agent
then keeps working with the built-in toolset alone.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


# Verbs that imply state mutation. When ``mcp_block_writes`` is on and no
# whitelist is configured, tools whose names contain these substrings get
# dropped so we never advertise DDL/DML primitives.
_WRITE_VERBS = (
    "write", "insert", "update", "delete", "drop", "create", "alter",
    "truncate", "grant", "revoke", "exec_sql", "execute_sql", "kill",
    "rename",
)


def _looks_writable(name: str) -> bool:
    """Cheap heuristic: True if the tool name suggests it mutates state."""
    lowered = name.lower()
    for verb in _WRITE_VERBS:
        if (
            f"_{verb}_" in lowered
            or f"-{verb}-" in lowered
            or lowered.startswith(f"{verb}_")
            or lowered.endswith(f"_{verb}")
            or lowered.startswith(f"{verb}-")
            or lowered.endswith(f"-{verb}")
        ):
            return True
    if lowered == "write" or "exec" in lowered:
        return True
    return False


class MCPLoader:
    """Process-wide loader for MCP-backed LangChain tools.

    Lifecycle:
        * ``await loader.load()`` is called once at app startup.
        * Subsequent calls return the cached tool list immediately.
        * :meth:`status` reports diagnostics for ``/health``.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._tools: list[Any] = []
        self._lock = asyncio.Lock()
        self._status: dict[str, Any] = {
            "enabled": self._settings.mcp_enabled,
            "configured": bool(self._settings.mcp_url) and self._settings.mcp_enabled,
            "connected": False,
            "server_name": self._settings.mcp_server_name,
            "url": self._settings.mcp_url,
            "transport": self._settings.mcp_transport.value
            if hasattr(self._settings.mcp_transport, "value")
            else str(self._settings.mcp_transport),
            "tool_count": 0,
            "tool_names": [],
            "error": None,
        }

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def load(self, *, force: bool = False) -> list[Any]:
        """Initialise the MCP connection exactly once.

        Returns the cached tool list (possibly empty). Subsequent calls
        return immediately unless ``force=True``.
        """
        async with self._lock:
            if self._tools and not force:
                return list(self._tools)

            if not self._settings.mcp_enabled:
                self._status["error"] = "MCP disabled (MCP_ENABLED=false)"
                logger.info("MCP: disabled by configuration (MCP_ENABLED=false)")
                return list(self._tools)

            if not self._settings.mcp_url:
                self._status["error"] = "MCP_URL is empty"
                logger.info("MCP: no MCP_URL configured, skipping MCP tool load")
                return list(self._tools)

            try:
                raw_tools = await self._fetch_tools()
                tools = self._filter_tools(raw_tools)
            except asyncio.TimeoutError:
                msg = (
                    f"MCP connect/list_tools timed out after "
                    f"{self._settings.mcp_connect_timeout_seconds}s "
                    f"(url={self._settings.mcp_url})"
                )
                logger.warning(msg)
                self._status["error"] = msg
                self._status["connected"] = False
                return list(self._tools)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "MCP load failed (url=%s): %s", self._settings.mcp_url, exc
                )
                self._status["error"] = f"{type(exc).__name__}: {exc}"
                self._status["connected"] = False
                return list(self._tools)

        self._apply_prefix(tools)
        self._tools = list(tools)
        self._status.update(
            connected=True,
            tool_count=len(tools),
            tool_names=[getattr(t, "name", "?") for t in tools],
            error=None,
        )
        prefix_note = (
            f" with prefix '{self._settings.mcp_tool_prefix}'"
            if self._settings.mcp_tool_prefix
            else ""
        )
        logger.info(
            "MCP loaded %d tool(s)%s from %s (%s)",
            len(tools),
            prefix_note,
            self._settings.mcp_url,
            self._settings.mcp_transport,
        )
        return list(self._tools)

    def list_tools(self) -> list[Any]:
        """Return a snapshot of currently cached MCP tools (safe across contexts)."""
        return list(self._tools)

    def status(self) -> dict[str, Any]:
        """Return a JSON-serialisable status snapshot suitable for ``/health``."""
        return dict(self._status)

    async def aclose(self) -> None:
        """Drop cached state and release any held resources."""
        async with self._lock:
            self._tools = []
            self._status["connected"] = False
            self._status["tool_count"] = 0
            self._status["tool_names"] = []
        logger.debug("MCP loader closed")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _fetch_tools(self) -> list[Any]:
        """Open an MCP session and return raw tool objects."""
        from langchain_mcp_adapters.client import MultiServerMCPClient

        cfg = self._settings
        label = cfg.mcp_server_name or "mcp"
        transport = (
            cfg.mcp_transport.value
            if hasattr(cfg.mcp_transport, "value")
            else str(cfg.mcp_transport)
        )

        connection: dict[str, Any] = {
            "url": cfg.mcp_url,
            "transport": transport,
        }
        if cfg.mcp_auth_token:
            connection["headers"] = {
                "Authorization": f"Bearer {cfg.mcp_auth_token}"
            }

        client = MultiServerMCPClient(connections={label: connection})

        # ``client.get_tools()`` is async; wrap in a timeout.
        timeout = max(cfg.mcp_connect_timeout_seconds, 1.0)
        raw_tools = await asyncio.wait_for(client.get_tools(), timeout=timeout)
        return list(raw_tools)

    def _filter_tools(self, tools: list[Any]) -> list[Any]:
        """Apply whitelist + max-tool cap + write-block heuristic."""
        cfg = self._settings

        allowed = [
            name.strip().lower()
            for name in (cfg.mcp_allowed_tools or [])
            if name and name.strip()
        ]

        # Empty list or ['*'] -> no whitelist, optional write-block.
        if not allowed or allowed == ["*"]:
            if cfg.mcp_block_writes:
                kept, dropped = [], []
                for tool in tools:
                    (dropped if _looks_writable(getattr(tool, "name", "")) else kept).append(tool)
                if dropped:
                    logger.info(
                        "MCP: filtered out write-capable tool(s): %s",
                        [getattr(t, "name", "?") for t in dropped],
                    )
                tools = kept
            return list(tools)[: cfg.mcp_max_tools]

        # Explicit whitelist.
        allowed_set = set(allowed)
        kept: list[Any] = []
        dropped: list[str] = []
        for tool in tools:
            name = getattr(tool, "name", "")
            if name.lower() in allowed_set:
                kept.append(tool)
            else:
                if name:
                    dropped.append(name)
        if dropped:
            logger.info("MCP: %d tool(s) filtered by whitelist: %s", len(dropped), dropped)
        return kept[: cfg.mcp_max_tools]

    def _apply_prefix(self, tools: list[Any]) -> None:
        """Mutates ``BaseTool.name`` in place to add the configured prefix."""
        prefix = self._settings.mcp_tool_prefix
        if not prefix:
            return
        for tool in tools:
            new_name = f"{prefix}{tool.name}"
            try:
                tool.name = new_name  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                logger.debug("Could not rename MCP tool %s; leaving as-is", tool.name)


# ----------------------------------------------------------------------
# module-level singleton
# ----------------------------------------------------------------------
_loader: MCPLoader | None = None


def get_loader() -> MCPLoader:
    """Return (and lazily construct) the process-wide :class:`MCPLoader`."""
    global _loader
    if _loader is None:
        _loader = MCPLoader()
    return _loader


async def init_mcp_tools(force: bool = False) -> list[Any]:
    """Initialise MCP tools once (idempotent unless ``force=True``)."""
    return await get_loader().load(force=force)


def list_mcp_tools() -> list[Any]:
    """Return currently cached MCP tools (empty until :func:`init_mcp_tools` runs)."""
    return get_loader().list_tools()


def mcp_tools_status() -> dict[str, Any]:
    """Return the public status payload for ``/health``."""
    return get_loader().status()


async def shutdown_mcp() -> None:
    """Drop cached MCP state. Called from FastAPI shutdown."""
    if _loader is not None:
        await _loader.aclose()


__all__ = [
    "MCPLoader",
    "get_loader",
    "init_mcp_tools",
    "list_mcp_tools",
    "mcp_tools_status",
    "shutdown_mcp",
]
