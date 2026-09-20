"""HTTP routes: /chat, /chat/stream, /threads, /threads/{id}, /health."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    MCPServerStatus,
    ThreadCreateResponse,
    ThreadHistory,
    ToolApprovalResume,
)
from app.config import get_settings
from app.memory.summary_store import get_summary_store
from app.services.agent_service import (
    get_history,
    get_thread_context_stats,
    invoke,
    new_thread_id,
    stream_events,
)
from app.tools.mcp_loader import mcp_tools_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _server_status_from(raw: dict[str, Any] | None) -> MCPServerStatus:
    """Convert an MCP status dict into a typed schema model."""
    raw = raw or {}
    return MCPServerStatus(
        enabled=bool(raw.get("enabled", False)),
        configured=bool(raw.get("configured", False)),
        connected=bool(raw.get("connected", False)),
        server_name=str(raw.get("server_name", "")),
        url=str(raw.get("url", "")),
        transport=str(raw.get("transport", "")),
        tool_count=int(raw.get("tool_count", 0)),
        tool_names=list(raw.get("tool_names", [])),
        error=raw.get("error"),
    )


@router.get("/health", response_model=HealthResponse, summary="Liveness/readiness probe")
async def health() -> HealthResponse:
    settings = get_settings()
    mcp_status = mcp_tools_status() or {}

    # Build per-server status map. Fall back to a single StarRocks entry when the
    # loader has not yet produced a multi-server payload.
    servers_raw: dict[str, dict[str, Any]] = mcp_status.get("servers") or {}
    if not servers_raw:
        servers_raw = {settings.mcp_server_name: mcp_status.get("mcp") or {}}
    servers = {
        name: _server_status_from(raw)
        for name, raw in servers_raw.items()
    }

    # If any enabled+configured server is not connected, the instance is degraded.
    overall = "ok"
    for status in servers.values():
        if status.enabled and status.configured and not status.connected:
            overall = "degraded"
            break

    # Preserve the legacy single-server entry for backwards compatibility.
    legacy_mcp = mcp_status.get("mcp") or {}
    legacy = _server_status_from(legacy_mcp)
    if not legacy.server_name and settings.mcp_server_name:
        legacy.server_name = settings.mcp_server_name
    if not legacy.url and settings.mcp_url:
        legacy.url = settings.mcp_url
    if not legacy.transport and settings.mcp_transport:
        legacy.transport = (
            settings.mcp_transport.value
            if hasattr(settings.mcp_transport, "value")
            else str(settings.mcp_transport)
        )
    if not legacy.configured and settings.mcp_enabled and settings.mcp_url:
        legacy.enabled = settings.mcp_enabled
        legacy.configured = True

    return HealthResponse(
        status=overall,
        llm_provider=settings.llm_provider.value,
        checkpointer=settings.checkpointer.value,
        hitl=settings.hitl,
        mcp=legacy,
        servers=servers,
    )


@router.post(
    "/threads",
    response_model=ThreadCreateResponse,
    summary="Create a new conversation thread",
)
async def create_thread() -> ThreadCreateResponse:
    """Returns a ``thread_id`` the client uses for subsequent chat calls."""
    return ThreadCreateResponse(thread_id=new_thread_id(), created_at=datetime.now(timezone.utc))


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send a message and get the full reply",
)
async def post_chat(req: ChatRequest) -> ChatResponse:
    if req.resume is None and not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")

    # Normalize Pydantic resume payload back to a plain dict for the service layer.
    resume_payload: dict[str, Any] | None = None
    if req.resume is not None:
        resume_payload = (
            req.resume.model_dump()
            if isinstance(req.resume, ToolApprovalResume)
            else dict(req.resume)
        )

    result = await invoke(
        user_message=req.message,
        thread_id=req.thread_id,
        user_id=req.user_id,
        metadata=req.metadata,
        resume=resume_payload,
    )
    return ChatResponse(
        thread_id=result["thread_id"],
        message=ChatMessage(**result["message"]),
        iterations=result["iterations"],
        tool_calls=result["tool_calls"],
        pending_approval=result.get("pending_approval"),
    )


@router.post(
    "/chat/stream",
    summary="Stream the agent reply as Server-Sent Events",
    response_class=StreamingResponse,
)
async def post_chat_stream(req: ChatRequest) -> StreamingResponse:
    """Streams typed SSE events.

    Frame types:
      - ``message``       — initial frame with ``thread_id``
      - ``token``         — incremental text token
      - ``tool_start``    — the agent is invoking a tool
      - ``tool_end``      — tool execution finished
      - ``tool_approval`` — the agent needs user approval for a sensitive tool
      - ``done``          — final frame with run metadata
      - ``error``         — error frame
      - ``end``           — sentinel instructing clients to close
    """
    if req.resume is None and not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")

    # Normalize Pydantic resume payload back to a plain dict for the service layer.
    resume_payload: dict[str, Any] | None = None
    if req.resume is not None:
        resume_payload = (
            req.resume.model_dump()
            if isinstance(req.resume, ToolApprovalResume)
            else dict(req.resume)
        )

    async def event_source() -> AsyncIterator[bytes]:
        async for ev in stream_events(
            user_message=req.message,
            thread_id=req.thread_id,
            user_id=req.user_id,
            metadata=req.metadata,
            resume=resume_payload,
        ):
            event = ev.get("event", "message")
            data = ev.get("data")
            payload = json.dumps(data, ensure_ascii=False, default=str)
            yield f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
        # Sentinel so clients can cleanly close.
        yield b"event: end\ndata: {}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadHistory,
    summary="Get the message history for a thread",
)
async def get_thread(thread_id: str) -> ThreadHistory:
    messages = await get_history(thread_id)
    return ThreadHistory(thread_id=thread_id, messages=[ChatMessage(**m) for m in messages])


@router.get(
    "/threads/{thread_id}/context-size",
    summary="Estimate the LLM context size for a thread",
)
async def get_thread_context_size(thread_id: str) -> dict[str, Any]:
    """Return approximate token/char statistics for the thread's context."""
    return await get_thread_context_stats(thread_id)


@router.get(
    "/threads/{thread_id}/summary",
    summary="Get the persisted summary for a thread",
)
async def get_thread_summary(thread_id: str) -> dict[str, Any]:
    """Return the stored conversation summary, if any."""
    store = get_summary_store()
    if store is None:
        raise HTTPException(
            status_code=503, detail="summary store is not enabled"
        )
    summary = await store.get_summary(thread_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="no summary found")
    return summary


