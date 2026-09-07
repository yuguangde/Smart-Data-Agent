"""Graph state definitions (extensible on top of MessagesState)."""
from __future__ import annotations

from typing import Any

from langgraph.graph import MessagesState
from pydantic import Field


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

    # Data-pipeline fields (program-driven SQL generation & execution)
    is_data_question: bool | None = None
    intent: str | None = None  # metric_analysis / free_exploration
    dsl: dict[str, Any] | None = None
    sql: str | None = None
    query_error: str | None = None
    execution_result: dict[str, Any] | None = None
    execution_error: str | None = None

    # Multi-step data analysis loop
    data_iterations: int = 0
    refined_question: str | None = None
    collected_results: list[dict[str, Any]] = Field(default_factory=list)
    data_sufficient: bool | None = None

    # General-agent fields
    pending_tool_calls: list[dict[str, Any]] | None = None

    # Follow-up handling
    is_metric_followup: bool | None = None


__all__ = ["AgentState"]
