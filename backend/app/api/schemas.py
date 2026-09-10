"""Pydantic request/response schemas for the HTTP chat API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant", "system", "tool", "ai", "human"]
    content: str = ""
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ToolApprovalResume(BaseModel):
    """Resume payload used when continuing from a tool-approval interrupt."""

    approved: bool = Field(
        ..., description="Whether the user approved the pending sensitive tool calls."
    )


class ChatRequest(BaseModel):
    """Request body for POST /chat and POST /chat/stream."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = Field(
        default=None, description="Reuse an existing thread for multi-turn chat."
    )
    message: str = Field(default="", min_length=0, max_length=32_000)
    user_id: str = Field(default="anonymous", max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    resume: ToolApprovalResume | dict[str, Any] | None = Field(
        default=None,
        description="Resume a paused graph (e.g. user approval for a tool call).",
    )


class ChatResponse(BaseModel):
    """Response body for POST /chat."""

    thread_id: str
    message: ChatMessage
    iterations: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    pending_approval: dict[str, Any] | None = Field(
        default=None,
        description="If present, the graph is paused waiting for tool approval.",
    )


class ThreadCreateResponse(BaseModel):
    """Response from POST /threads."""

    thread_id: str
    created_at: datetime


class ThreadHistory(BaseModel):
    """Listing of messages associated with a thread."""

    thread_id: str
    messages: list[ChatMessage]


class MCPServerStatus(BaseModel):
    """Subset of MCP client status surfaced on /health."""

    enabled: bool = Field(default=False)
    configured: bool = Field(default=False)
    connected: bool = Field(default=False)
    server_name: str = Field(default="")
    url: str = Field(default="")
    transport: str = Field(default="")
    tool_count: int = Field(default=0)
    tool_names: list[str] = Field(default_factory=list)
    error: str | None = Field(default=None)


class ReviewRequest(BaseModel):
    """Request body for POST /review and POST /review/stream."""

    thread_id: str = Field(..., description="Primary thread whose final report should be reviewed.")
    strategy: Literal["reexecute", "resynthesize"] = Field(
        default="reexecute",
        description="reexecute = rerun the data query workflow; resynthesize = rewrite the report from existing results.",
    )


class ReviewComparison(BaseModel):
    """Result of comparing the primary and review reports."""

    verdict: Literal["consistent", "partial", "inconsistent"] = "partial"
    summary: str = ""
    differences: list[dict[str, Any]] = Field(default_factory=list)


class ReviewResponse(BaseModel):
    """Response body for POST /review."""

    primary_thread_id: str
    review_thread_id: str
    user_question: str
    main_report: str
    review_report: str
    comparison: ReviewComparison


class ReviewSummary(BaseModel):
    """Lightweight review record returned by GET /threads/{id}/reviews."""

    review_id: str
    thread_id: str
    strategy: str
    review_model: str
    verdict: str
    summary: str
    created_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    llm_provider: str
    checkpointer: str
    hitl: bool
    mcp: MCPServerStatus = Field(default_factory=MCPServerStatus)


__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ToolApprovalResume",
    "ThreadCreateResponse",
    "ThreadHistory",
    "ReviewRequest",
    "ReviewComparison",
    "ReviewResponse",
    "ReviewSummary",
    "HealthResponse",
]
