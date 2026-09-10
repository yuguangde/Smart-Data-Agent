"""Service layer for the review agent.

Fetches the primary agent's report, runs the review agent in a separate thread,
and compares the resulting reports.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.comparator import compare_reports
from app.agent.graph import get_compiled_graph
from app.agent.nodes import _utc_now_iso
from app.agent.review_graph import get_compiled_review_graph
from app.config import get_settings
from app.memory.review_store import get_review_store
from app.services.agent_service import _last_ai_message, _plain_str

logger = logging.getLogger(__name__)


def _last_report(messages: list) -> tuple[int, str]:
    """Return the index and content of the last assistant message that has no tool_calls."""
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            return idx, _plain_str(msg.content)
    raise ValueError("Thread has no final assistant report")


def _preceding_user_question(messages: list, report_index: int) -> str:
    """Return the content of the human message right before the report."""
    for idx in range(report_index - 1, -1, -1):
        if isinstance(messages[idx], HumanMessage):
            return _plain_str(messages[idx].content)
    raise ValueError("Thread has no preceding user question")


async def fetch_primary_context(thread_id: str) -> tuple[str, str]:
    """Return (user_question, main_report) for the given primary thread."""
    graph = get_compiled_graph()
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    values = getattr(snapshot, "values", None) or {}
    messages = list(values.get("messages", []))
    if not messages:
        raise ValueError(f"Thread {thread_id!r} has no messages")
    report_index, main_report = _last_report(messages)
    user_question = _preceding_user_question(messages, report_index)
    return user_question, main_report


def _make_review_initial_state(user_question: str, strategy: str) -> dict[str, Any]:
    return {
        "messages": [
            HumanMessage(
                content=user_question,
                additional_kwargs={"timestamp": _utc_now_iso()},
            ),
        ],
        "iterations": 0,
        "user_id": "review",
        "metadata": {"strategy": strategy},
    }


async def run_review(
    thread_id: str,
    *,
    strategy: str = "reexecute",
) -> dict[str, Any]:
    """Run the review agent synchronously and return the comparison result."""
    user_question, main_report = await fetch_primary_context(thread_id)
    review_thread_id = f"{thread_id}-review-{uuid.uuid4().hex[:8]}"

    graph = get_compiled_review_graph()
    config = {"configurable": {"thread_id": review_thread_id, "user_id": "review"}}
    input_value = _make_review_initial_state(user_question, strategy)

    result = await graph.ainvoke(input_value, config=config)
    _, review_report = _last_report(result.get("messages", []))

    comparison = await compare_reports(main_report, review_report, user_question)

    review_model = get_settings().review_llm_provider.value
    store = get_review_store()
    if store is not None:
        try:
            await store.save_review(
                thread_id=thread_id,
                review_thread_id=review_thread_id,
                strategy=strategy,
                main_report=main_report,
                review_report=review_report,
                comparison=comparison,
                review_model=review_model,
            )
        except Exception as exc:
            logger.warning("Failed to persist review: %s", exc)

    return {
        "primary_thread_id": thread_id,
        "review_thread_id": review_thread_id,
        "user_question": user_question,
        "main_report": main_report,
        "review_report": review_report,
        "comparison": comparison,
    }


async def stream_review(
    thread_id: str,
    *,
    strategy: str = "reexecute",
) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE-style events describing the review run and final comparison."""
    user_question, main_report = await fetch_primary_context(thread_id)
    review_thread_id = f"{thread_id}-review-{uuid.uuid4().hex[:8]}"

    graph = get_compiled_review_graph()
    config = {"configurable": {"thread_id": review_thread_id, "user_id": "review"}}
    input_value = _make_review_initial_state(user_question, strategy)

    yield {
        "event": "review_started",
        "data": {"review_thread_id": review_thread_id},
    }

    review_report = ""
    try:
        async for ev in graph.astream_events(input_value, config=config, version="v2"):
            kind = ev.get("event")
            name = ev.get("name", "")
            data = ev.get("data", {}) or {}

            if kind == "on_chat_model_stream":
                metadata = ev.get("metadata", {})
                node = metadata.get("langgraph_node")
                if node != "agent":
                    continue
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                text = _plain_str(getattr(chunk, "content", "") or "")
                if text:
                    yield {"event": "review_token", "data": text}
                tc_chunks = getattr(chunk, "tool_call_chunks", None)
                if tc_chunks:
                    for tc in tc_chunks:
                        if tc.get("name"):
                            tc_args = tc.get("args")
                            yield {
                                "event": "review_tool_start",
                                "data": {
                                    "id": tc.get("id"),
                                    "name": tc.get("name"),
                                    "input": tc_args if isinstance(tc_args, dict) else {},
                                },
                            }

            elif kind == "on_tool_start":
                # review_tool_start is already emitted via tool_call_chunks above.
                pass

            elif kind == "on_tool_end":
                output = data.get("output", "")
                tool_call_id: str | None = None
                if isinstance(output, ToolMessage):
                    tool_call_id = getattr(output, "tool_call_id", None)
                    output = output.content
                yield {
                    "event": "review_tool_end",
                    "data": {
                        "id": tool_call_id,
                        "output": _plain_str(output),
                    },
                }

            elif kind == "on_chain_end" and name == "LangGraph":
                output = data.get("output", {})
                if hasattr(output, "values"):
                    output = output.values
                last = _last_ai_message(output) if isinstance(output, dict) else None
                if last:
                    yield {"event": "review_message", "data": last}
                    review_report = _plain_str(last.get("content", ""))
                yield {
                    "event": "review_done",
                    "data": {"review_thread_id": review_thread_id},
                }

    except Exception as exc:
        logger.exception("Review streaming failed: %s", exc)
        yield {"event": "review_error", "data": str(exc)}
        return

    if review_report:
        try:
            comparison = await compare_reports(main_report, review_report, user_question)
        except Exception as exc:
            logger.warning("Report comparison failed: %s", exc)
            comparison = {
                "verdict": "partial",
                "summary": "复核报告已生成，但对比过程失败。",
                "differences": [],
            }
        yield {"event": "review_comparison", "data": comparison}

        review_model = get_settings().review_llm_provider.value
        store = get_review_store()
        if store is not None:
            try:
                await store.save_review(
                    thread_id=thread_id,
                    review_thread_id=review_thread_id,
                    strategy=strategy,
                    main_report=main_report,
                    review_report=review_report,
                    comparison=comparison,
                    review_model=review_model,
                )
            except Exception as exc:
                logger.warning("Failed to persist review stream result: %s", exc)

    yield {"event": "review_end", "data": {"review_thread_id": review_thread_id}}


__all__ = ["run_review", "stream_review", "fetch_primary_context"]
