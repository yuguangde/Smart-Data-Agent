"""Graph state definitions (extensible on top of MessagesState)."""
from __future__ import annotations

from typing import Any

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """State flowing through the LangGraph.

    `messages` is the canonical conversation history (Human / AI / Tool).
    `user_id` and `metadata` allow caller correlation without polluting messages.
    `iterations` guards against runaway tool loops via `max_iterations`.
    """

    user_id: str = "anonymous"
    metadata: dict[str, Any] = {}
    iterations: int = 0
    final_answer: str = ""


__all__ = ["AgentState"]
