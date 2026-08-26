"""WebSocket route for low-latency bidirectional chat."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.agent_service import (
    get_history,
    new_thread_id,
    stream_events,
)

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
    # token / fallback
    return {"type": "token", "data": data if isinstance(data, str) else str(data)}


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    """Bidirectional WebSocket session.

    Wire protocol (one JSON frame per message):
        Inbound  -> {"type": "message"|"history"|"reset"|"ping", ...}
        Outbound -> {"type": "thread"|"token"|"tool_start"|"tool_end"|"done"|"error"|"history"|"pong", ...}
    """
    await ws.accept()
    thread_id: str | None = None

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
                thread_id = new_thread_id()
                await ws.send_json({"type": "thread", "thread_id": thread_id})
                continue

            if kind == "history":
                tid = payload.get("thread_id") or thread_id
                if not tid:
                    await ws.send_json({"type": "error", "data": "thread_id required"})
                    continue
                msgs = await get_history(tid)
                await ws.send_json({"type": "history", "thread_id": tid, "data": msgs})
                continue

            if kind == "message":
                content = (payload.get("content") or "").strip()
                if not content:
                    await ws.send_json({"type": "error", "data": "content required"})
                    continue
                tid = payload.get("thread_id") or thread_id or new_thread_id()
                thread_id = tid
                await ws.send_json({"type": "thread", "thread_id": tid})

                try:
                    async for ev in stream_events(
                        user_message=content,
                        thread_id=tid,
                        user_id=payload.get("user_id", "anonymous"),
                        metadata=payload.get("metadata") or {},
                    ):
                        await ws.send_json(_to_ws_frame(ev))
                except Exception as exc:
                    logger.exception("WS stream failed: %s", exc)
                    await ws.send_json({"type": "error", "data": str(exc)})
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
