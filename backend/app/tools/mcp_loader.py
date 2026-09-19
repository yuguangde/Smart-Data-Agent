"""Process-wide cache of MCP-sourced tools.

This module owns the *runtime* state for MCP-backed tools: it lazily
connects to configured MCP servers (currently an optional StarRocks MCP
instance and an optional DoSI MCP instance) and exposes the resulting
LangChain ``BaseTool`` list to the rest of the application.

``app.tools.get_all_tools()`` calls :func:`list_tools` here to merge MCP
tools with the built-in toolset. Initialisation is meant to run during
FastAPI lifespan startup (see ``app.main.lifespan``) so the first chat
request does not pay the connect handshake.

Failure modes are deliberately non-fatal: if an MCP server is unreachable
or returns no tools, we log a warning and return an empty list for that
server. The agent then keeps working with the built-in toolset alone.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import MCPTransport, Settings, get_settings

logger = logging.getLogger(__name__)


# Verbs that imply state mutation. When ``mcp_block_writes`` is on and no
# whitelist is configured, tools whose names contain these substrings get
# dropped so we never advertise DDL/DML primitives.
_WRITE_VERBS = (
    "write", "insert", "update", "delete", "drop", "create", "alter",
    "truncate", "grant", "revoke", "exec_sql", "execute_sql", "kill",
    "rename",
)


@dataclass
class _ServerConfig:
    """Connection and filtering configuration for one MCP server."""

    name: str
    url: str
    transport: MCPTransport
    prefix: str
    allowed_tools: list[str]
    block_writes: bool
    max_tools: int
    auth_token: str
    connect_timeout_seconds: float
    call_timeout_seconds: float


@dataclass
class _ServerStatus:
    """Runtime status for one MCP server."""

    enabled: bool = False
    configured: bool = False
    connected: bool = False
    server_name: str = ""
    url: str = ""
    transport: str = ""
    tool_count: int = 0
    tool_names: list[str] = field(default_factory=list)
    error: str | None = None


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


def _build_server_configs(settings: Settings) -> list[_ServerConfig]:
    """Return the list of MCP server configurations the loader should use.

    For backwards compatibility, the original StarRocks MCP settings are
    preserved. An additional DoSI MCP server is added when
    ``dosi_mcp_enabled`` is true.
    """
    configs: list[_ServerConfig] = []

    # Legacy StarRocks MCP configuration.
    if settings.mcp_enabled and settings.mcp_url:
        configs.append(
            _ServerConfig(
                name=settings.mcp_server_name or "mcp",
                url=settings.mcp_url,
                transport=settings.mcp_transport,
                prefix=settings.mcp_tool_prefix or "",
                allowed_tools=list(settings.mcp_allowed_tools or []),
                block_writes=settings.mcp_block_writes,
                max_tools=settings.mcp_max_tools,
                auth_token=settings.mcp_auth_token,
                connect_timeout_seconds=settings.mcp_connect_timeout_seconds,
                call_timeout_seconds=settings.mcp_call_timeout_seconds,
            )
        )

    # DoSI semantic-layer MCP configuration.
    if settings.dosi_mcp_enabled and settings.dosi_mcp_url:
        configs.append(
            _ServerConfig(
                name=settings.dosi_server_name or "dosi",
                url=settings.dosi_mcp_url,
                transport=settings.dosi_mcp_transport,
                prefix=settings.dosi_tool_prefix or "",
                allowed_tools=list(settings.dosi_allowed_tools or []),
                block_writes=False,
                max_tools=settings.mcp_max_tools,
                auth_token="",
                connect_timeout_seconds=settings.mcp_connect_timeout_seconds,
                call_timeout_seconds=settings.dosi_query_timeout_seconds,
            )
        )

    return configs


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
        self._statuses: dict[str, _ServerStatus] = {}
        self._configs = _build_server_configs(self._settings)
        for cfg in self._configs:
            self._statuses[cfg.name] = _ServerStatus(
                enabled=True,
                configured=bool(cfg.url),
                server_name=cfg.name,
                url=cfg.url,
                transport=(
                    cfg.transport.value
                    if hasattr(cfg.transport, "value")
                    else str(cfg.transport)
                ),
            )

    @classmethod
    def current_configs(cls) -> list[_ServerConfig]:
        """Return the currently configured MCP server list."""
        return _build_server_configs(get_settings())

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def load(self, *, force: bool = False) -> list[Any]:
        """Initialise MCP connections exactly once.

        Returns the cached tool list (possibly empty). Subsequent calls
        return immediately unless ``force=True``.
        """
        async with self._lock:
            if self._tools and not force:
                return list(self._tools)

            all_tools: list[Any] = []
            for cfg in self._configs:
                server_tools = await self._load_one(cfg)
                all_tools.extend(server_tools)

            self._tools = list(all_tools)
            return list(self._tools)

    def list_tools(self) -> list[Any]:
        """Return a snapshot of currently cached MCP tools (safe across contexts)."""
        return list(self._tools)

    def status(self) -> dict[str, Any]:
        """Return a JSON-serialisable status snapshot suitable for ``/health``.

        Includes a per-server breakdown plus a legacy ``mcp`` entry for
        backwards compatibility.
        """
        by_server = {
            name: {
                "enabled": s.enabled,
                "configured": s.configured,
                "connected": s.connected,
                "server_name": s.server_name,
                "url": s.url,
                "transport": s.transport,
                "tool_count": s.tool_count,
                "tool_names": list(s.tool_names),
                "error": s.error,
            }
            for name, s in self._statuses.items()
        }
        # Preserve the existing response shape for callers that expect a flat
        # mcp status object.
        legacy = by_server.get(self._settings.mcp_server_name) if self._settings.mcp_enabled else None
        return {
            "servers": by_server,
            "mcp": legacy or {
                "enabled": self._settings.mcp_enabled,
                "configured": bool(self._settings.mcp_url),
                "connected": False,
                "server_name": self._settings.mcp_server_name,
                "url": self._settings.mcp_url,
                "transport": (
                    self._settings.mcp_transport.value
                    if hasattr(self._settings.mcp_transport, "value")
                    else str(self._settings.mcp_transport)
                ),
                "tool_count": 0,
                "tool_names": [],
                "error": None,
            },
        }

    async def aclose(self) -> None:
        """Drop cached state and release any held resources."""
        async with self._lock:
            self._tools = []
            for s in self._statuses.values():
                s.connected = False
                s.tool_count = 0
                s.tool_names = []
        logger.debug("MCP loader closed")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _load_one(self, cfg: _ServerConfig) -> list[Any]:
        """Fetch and filter tools from a single MCP server."""
        status = self._statuses[cfg.name]
        try:
            raw_tools = await self._fetch_tools(cfg)
            tools = self._filter_tools(raw_tools, cfg)
        except asyncio.TimeoutError:
            msg = (
                f"MCP connect/list_tools timed out after "
                f"{cfg.connect_timeout_seconds}s "
                f"(url={cfg.url})"
            )
            logger.warning(msg)
            status.error = msg
            status.connected = False
            return []
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "MCP load failed for %s (url=%s): %s",
                cfg.name, cfg.url, exc,
            )
            status.error = f"{type(exc).__name__}: {exc}"
            status.connected = False
            return []

        self._apply_prefix(tools, cfg.prefix)
        status.connected = True
        status.tool_count = len(tools)
        status.tool_names = [getattr(t, "name", "?") for t in tools]
        status.error = None
        prefix_note = f" with prefix '{cfg.prefix}'" if cfg.prefix else ""
        logger.info(
            "MCP loaded %d tool(s)%s from %s (%s)",
            len(tools),
            prefix_note,
            cfg.name,
            cfg.url,
        )
        return list(tools)

    async def _fetch_tools(self, cfg: _ServerConfig) -> list[Any]:
        """Open an MCP session and return raw tool objects."""
        from langchain_mcp_adapters.client import MultiServerMCPClient

        transport = (
            cfg.transport.value
            if hasattr(cfg.transport, "value")
            else str(cfg.transport)
        )
        connection: dict[str, Any] = {
            "url": cfg.url,
            "transport": transport,
        }
        if cfg.auth_token:
            connection["headers"] = {
                "Authorization": f"Bearer {cfg.auth_token}"
            }

        client = MultiServerMCPClient(connections={cfg.name: connection})
        timeout = max(cfg.connect_timeout_seconds, 1.0)
        raw_tools = await asyncio.wait_for(client.get_tools(), timeout=timeout)
        return list(raw_tools)

    def _filter_tools(self, tools: list[Any], cfg: _ServerConfig) -> list[Any]:
        """Apply whitelist + max-tool cap + write-block heuristic."""
        allowed = [name.strip().lower() for name in cfg.allowed_tools if name and name.strip()]

        # Empty list -> no whitelist; optional write-block.
        if not allowed:
            if cfg.block_writes:
                kept, dropped = [], []
                for tool in tools:
                    (dropped if _looks_writable(getattr(tool, "name", "")) else kept).append(tool)
                if dropped:
                    logger.info(
                        "MCP: filtered out write-capable tool(s): %s",
                        [getattr(t, "name", "?") for t in dropped],
                    )
                tools = kept
            return list(tools)[: cfg.max_tools]

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
            logger.info(
                "MCP: %d tool(s) filtered by whitelist for %s: %s",
                len(dropped), cfg.name, dropped,
            )
        return kept[: cfg.max_tools]

    def _apply_prefix(self, tools: list[Any], prefix: str) -> None:
        """Mutates ``BaseTool.name`` in place to add the configured prefix."""
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
    """Drop cached state. Called from FastAPI shutdown."""
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
