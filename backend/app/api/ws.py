"""WebSocket route for low-latency bidirectional chat."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.agent_service import get_history, new_thread_id, stream_events

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ws-chat"])


def _to_ws_frame(ev: dict) -> dict:
    """Translate service-layer events to the WebSocket wire format."""
    kind = ev.get("event")
    data = ev.get("data")
    if kind == "message" and isinstance(data, dict) and "thread_id" in data:
        return {"type": "thread", "thread_id": data["thread_id"]}
    if kind == "tool_start":
        return {"type": "tool_start", **data}
    if kind == "tool_end":
        return {"type": "tool_end", **data}
    if kind == "done":
        return {"type": "done", **data}
    if kind == "error":
        return {"type": "error", "data": str(data)}
    if kind == "tool_approval":
        return {"type": "tool_approval", "data": data}
    # token / fallback
    return {"type": "token", "data": data if isinstance(data, str) else str(data)}


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    """Bidirectional WebSocket session.

    Wire protocol (one JSON frame per message):
        Inbound  -> {"type": "message"|"history"|"reset"|"ping"|"tool_approval", ...}
        Outbound -> {"type": "thread"|"token"|"tool_start"|"tool_end"|"done"|"error"|"history"|"pong"|"tool_approval", ...}
    """
    await ws.accept()
    thread_id: str | None = None

    async def _send_stream(
        *,
        content: str | None = None,
        user_id: str = "anonymous",
        metadata: dict | None = None,
        resume: dict | None = None,
    ) -> bool:
        """Run one streaming pass. Returns True if it paused for approval."""
        try:
            async for ev in stream_events(
                user_message=content,
                thread_id=thread_id,
                user_id=user_id,
                metadata=metadata,
                resume=resume,
            ):
                await ws.send_json(_to_ws_frame(ev))
                if ev.get("event") == "tool_approval":
                    return True
        except Exception as exc:
            logger.exception("WS stream failed: %s", exc)
            await ws.send_json({"type": "error", "data": str(exc)})
        return False

    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "data": "invalid JSON"})
                continue

            kind = payload.get("type")

            if kind == "ping":
                await ws.send_json({"type": "pong"})
                continue

            if kind == "reset":
                thread_id = None
                await ws.send_json({"type": "thread", "thread_id": None})
                continue

            if kind == "history":
                tid = payload.get("thread_id") or thread_id
                if not tid:
                    await ws.send_json({"type": "error", "data": "thread_id required"})
                    continue
                try:
                    msgs = await get_history(tid)
                    await ws.send_json({"type": "history", "thread_id": tid, "data": msgs})
                except Exception as exc:
                    await ws.send_json({"type": "error", "data": f"history failed: {exc}"})
                continue

            if kind == "tool_approval":
                # User responded to a pending tool-approval request.
                approved = bool(payload.get("approved", False))
                await _send_stream(resume={"approved": approved})
                continue

            if kind == "message":
                content = (payload.get("content") or "").strip()
                if not content:
                    await ws.send_json({"type": "error", "data": "content required"})
                    continue
                thread_id = payload.get("thread_id") or thread_id
                if not thread_id:
                    thread_id = new_thread_id()

                user_id = payload.get("user_id") or "anonymous"
                metadata = payload.get("metadata") or {}

                await ws.send_json({"type": "thread", "thread_id": thread_id})
                needs_approval = await _send_stream(
                    content=content, user_id=user_id, metadata=metadata
                )

                if needs_approval:
                    # Block until the user approves or rejects the sensitive tool.
                    while True:
                        try:
                            raw2 = await ws.receive_text()
                        except WebSocketDisconnect:
                            return
                        try:
                            payload2 = json.loads(raw2)
                        except json.JSONDecodeError:
                            await ws.send_json({"type": "error", "data": "invalid JSON"})
                            continue

                        if payload2.get("type") == "ping":
                            await ws.send_json({"type": "pong"})
                            continue

                        if payload2.get("type") == "tool_approval":
                            approved = bool(payload2.get("approved", False))
                            await _send_stream(resume={"approved": approved})
                            break

                        await ws.send_json({"type": "error", "data": "waiting for tool_approval response"})
                continue

            await ws.send_json({"type": "error", "data": f"unknown message type: {kind!r}"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("WebSocket session crashed: %s", exc)
        try:
            await ws.send_json({"type": "error", "data": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
