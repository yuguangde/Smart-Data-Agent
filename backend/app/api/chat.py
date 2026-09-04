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
from app.services.agent_service import (
    get_history,
    invoke,
    new_thread_id,
    stream_events,
)
from app.tools.mcp_loader import mcp_tools_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.get("/health", response_model=HealthResponse, summary="Liveness/readiness probe")
async def health() -> HealthResponse:
    settings = get_settings()
    mcp_payload = mcp_tools_status() or {}
    mcp_block_required = bool(
        settings.mcp_enabled and settings.mcp_url
    )
    mcp_connected = bool(mcp_payload.get("connected"))
    overall = (
        "degraded"
        if mcp_block_required and not mcp_connected
        else "ok"
    )
    return HealthResponse(
        status=overall,
        llm_provider=settings.llm_provider.value,
        checkpointer=settings.checkpointer.value,
        hitl=settings.hitl,
        mcp=MCPServerStatus(
            enabled=bool(settings.mcp_enabled),
            configured=bool(settings.mcp_enabled and settings.mcp_url),
            connected=mcp_connected,
            server_name=str(
                mcp_payload.get("server_name") or settings.mcp_server_name
            ),
            url=str(mcp_payload.get("url") or settings.mcp_url),
            transport=str(
                mcp_payload.get("transport") or settings.mcp_transport
            ),
            tool_count=int(mcp_payload.get("tool_count", 0)),
            tool_names=list(mcp_payload.get("tool_names", [])),
            error=mcp_payload.get("error"),
        ),
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


