"""FastAPI route collection.

All routes are mounted under the /api prefix so that the frontend
(VITE_API_BASE=/api in vite.config.ts) lines up with the backend.
The full service surface is therefore:

    POST  /api/threads             -> create a new conversation thread
    POST  /api/chat                -> blocking chat completion
    POST  /api/chat/stream         -> SSE streaming chat completion
    GET   /api/threads/{thread_id} -> thread history
    GET   /api/health              -> liveness/readiness probe
    WS    /api/ws/chat             -> bidirectional WebSocket chat
"""
from fastapi import APIRouter

from app.api.chat import router as chat_router
from app.api.ws import router as ws_router

# All endpoints are namespaced under /api so the frontend Proxy/api/*
# in vite.config.ts matches cleanly.
api_router = APIRouter(prefix="/api")
api_router.include_router(chat_router)
api_router.include_router(ws_router)

__all__ = ["api_router"]
