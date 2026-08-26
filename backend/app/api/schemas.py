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


class ChatRequest(BaseModel):
    """Request body for POST /chat and POST /chat/stream."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = Field(
        default=None, description="Reuse an existing thread for multi-turn chat."
    )
    message: str = Field(min_length=1, max_length=32_000)
    user_id: str = Field(default="anonymous", max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Response body for POST /chat."""

    thread_id: str
    message: ChatMessage
    iterations: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class ThreadCreateResponse(BaseModel):
    """Response from POST /threads."""

    thread_id: str
    created_at: datetime


class ThreadHistory(BaseModel):
    """Listing of messages associated with a thread."""

    thread_id: str
    messages: list[ChatMessage]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    llm_provider: str
    checkpointer: str
    hitl: bool


__all__ = [
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ThreadCreateResponse",
    "ThreadHistory",
    "HealthResponse",
]
