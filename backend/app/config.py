"""Application configuration via pydantic-settings (.env).

Adds MCP (Model Context Protocol) server settings on top of the previous
LLM/database/checkpointer config. Settings are validated at process start so
misconfigured MCP URLs don't show up as runtime errors deep in a chat
request — only on app startup (lifespan).
"""
from __future__ import annotations

import logging
import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class LLMProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"


class CheckpointerKind(StrEnum):
    MEMORY = "memory"
    SQLITE = "sqlite"


class MCPTransport(StrEnum):
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Strongly typed application settings sourced from the environment / .env."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -------- Server --------
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="INFO")
    app_name: str = Field(default="Smart Data Agent")
    app_version: str = Field(default="0.1.0")
    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["*"])

    # -------- LLM --------
    llm_provider: LLMProvider = Field(default=LLMProvider.OPENAI)

    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")
    openai_base_url: str = Field(default="")
    openai_temperature: float = Field(default=0.7)

    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-4-5")

    deepseek_api_key: str = Field(default="")
    deepseek_base_url: str = Field(default="https://api.deepseek.com")
    deepseek_model: str = Field(default="deepseek-chat")

    qwen_api_key: str = Field(default="")
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    qwen_model: str = Field(default="qwen-plus")

    max_tokens: int = Field(default=2048)
    max_iterations: int = Field(default=8)

    # -------- Memory --------
    checkpointer: CheckpointerKind = Field(default=CheckpointerKind.MEMORY)
    sqlite_path: str = Field(default=str(BASE_DIR / "data" / "chat.db"))

    # -------- Behavior --------
    hitl: bool = Field(
        default=False,
        description="Enable Human-in-the-Loop interrupt before agent node.",
    )

    # -------- MCP (Model Context Protocol) servers --------
    # Connect a remote MCP server (e.g. an mcp-server-starrocks instance
    # running on http://host:8000/mcp) and expose its tools to the agent.
    # Failures are non-fatal: the app starts without MCP tools if the server
    # is unreachable, toggling degraded on /health instead of crashing.
    mcp_enabled: bool = Field(
        default=False,
        description="Master switch for MCP-backed tools.",
    )
    mcp_url: str = Field(
        default="",
        description="Base URL of the MCP server (streamable_http / sse transport).",
    )
    mcp_transport: MCPTransport = Field(
        default=MCPTransport.STREAMABLE_HTTP,
        description="Transport protocol. stdio is not configured via env.",
    )
    mcp_server_name: str = Field(
        default="starrocks",
        description="Logical server label, used in logs and as tool-name prefix.",
    )
    mcp_tool_prefix: str = Field(
        default="starrocks_",
        description=(
            "Prefix prepended to every exposed MCP tool name. Leave empty to use the "
            "raw server-side names. Set to '' (empty) to disable prefixing."
        ),
    )
    mcp_allowed_tools: list[str] = Field(
        default_factory=lambda: ["read_query", "table_overview", "db_overview"],
        description=(
            "Whitelist of MCP tool names to expose (matched without the prefix). "
            "Shipped default targets StarRocks' read-only tools (SELECT/describe/overview). "
            "Use ['*'] (or leave unset) to accept every tool the server reports, then "
            "mcp_block_writes reduces risk by filtering common write verbs."
        ),
    )
    mcp_block_writes: bool = Field(
        default=True,
        description=(
            "When active and mcp_allowed_tools=['*'], drop tools whose name matches "
            "common DDL/DML verbs (insert/update/delete/drop/create/alter/...)."
        ),
    )
    mcp_max_tools: int = Field(
        default=64,
        description="Hard cap on tools loaded from MCP (prevents runaway tool lists).",
    )
    mcp_connect_timeout_seconds: float = Field(
        default=15.0,
        description="Timeout for the MCP connect / list_tools handshake.",
    )
    mcp_call_timeout_seconds: float = Field(
        default=60.0,
        description="Per-call timeout applied to MCP tool invocations.",
    )
    mcp_auth_token: str = Field(
        default="",
        description=(
            "Optional bearer token sent to the MCP server via Authorization header. "
            "Empty disables auth (acceptable for trusted local MCP servers)."
        ),
    )

    # -------- LangSmith --------
    langsmith_tracing: bool = Field(default=False)
    langsmith_api_key: str = Field(default="")
    langsmith_project: str = Field(default="smart-data-agent")

    @field_validator("mcp_allowed_tools", mode="before")
    @classmethod
    def _split_allowed_tools_csv(cls, v):
        """Accept both JSON lists and plain CSV strings from .env.

        Without this, ``MCP_ALLOWED_TOOLS=read_query,table_overview`` would
        raise a pydantic validation error because BaseSettings tries to JSON-
        decode list values.
        """
        if isinstance(v, str):
            stripped = v.strip()
            if not stripped:
                return []
            # Tolerate a JSON-looking list for symmetry with other settings.
            if stripped.startswith("[") and stripped.endswith("]"):
                return v
            return [s.strip() for s in stripped.split(",") if s.strip()]
        return v

    @field_validator("mcp_tool_prefix")
    @classmethod
    def _validate_tool_prefix(cls, v: str) -> str:
        # Empty string is allowed as "no prefix"; anything else must be Python
        # identifier-safe so the LLM can refer to the resulting tool name.
        if v and not v.replace("_", "").isalnum():
            raise ValueError(
                f"mcp_tool_prefix must be empty or alphanumeric (with optional '_'), got {v!r}"
            )
        return v

    @property
    def sqlite_path_resolved(self) -> Path:
        path = Path(self.sqlite_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    settings = Settings()
    _apply_langsmith_env(settings)
    return settings


def _apply_langsmith_env(s: Settings) -> None:
    """Bridge pydantic settings into the env vars LangSmith SDK reads."""
    if s.langsmith_tracing:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        if s.langsmith_api_key:
            os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key
        if s.langsmith_project:
            os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
