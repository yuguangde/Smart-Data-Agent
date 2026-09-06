"""Tests for the human-in-the-loop review flow on sensitive tools."""
from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool


class _FakeLLM:
    """Async LLM whose ainvoke returns staged messages across turns."""

    def __init__(
        self,
        responses: list[tuple[str, list[dict[str, Any]]]] | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._idx = 0

    async def ainvoke(self, messages: list[Any], **kwargs: Any) -> AIMessage:
        if self._idx < len(self._responses):
            content, tool_calls = self._responses[self._idx]
            self._idx += 1
            return AIMessage(content=content, tool_calls=tool_calls)
        # Default final response once the staged list is exhausted.
        return AIMessage(content="Done.")

    def bind_tools(self, tools: list[Any]) -> "_FakeLLM":
        return self


@pytest.fixture
def fake_read_file(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Install a fake read_file tool and return its call log."""
    calls: list[dict[str, Any]] = []

    @tool
    def read_file(path: str) -> str:
        """Fake file reader for HIL tests."""
        calls.append({"path": path})
        return f"content of {path}"

    @tool
    def calculator(expression: str) -> str:
        """Fake calculator."""
        return "42"

    def fake_get_llm_tools() -> list[Any]:
        return [read_file, calculator]

    monkeypatch.setattr("app.tools.get_llm_tools", fake_get_llm_tools)
    monkeypatch.setattr("app.agent.nodes.get_llm_tools", fake_get_llm_tools)

    return calls


def _build_graph(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    tool_calls: list[dict[str, Any]],
) -> None:
    """Patch the LLM and clear the compiled-graph cache so the graph uses fakes."""
    from app.agent import graph as graph_mod
    from app.agent import nodes
    from app.agent import query_nodes

    fake_agent_llm = _FakeLLM(
        responses=[
            ("I will read a file.", tool_calls),
            (content, []),
        ]
    )
    fake_router_llm = _FakeLLM(
        responses=[(json.dumps({"is_data_question": False}), [])]
    )
    monkeypatch.setattr(nodes, "get_llm", lambda *args, **kwargs: fake_agent_llm)
    monkeypatch.setattr(query_nodes, "get_llm", lambda *args, **kwargs: fake_router_llm)
    graph_mod.get_compiled_graph.cache_clear()


@pytest.mark.anyio
async def test_tool_review_interrupts_for_read_file(
    monkeypatch: pytest.MonkeyPatch,
    fake_read_file: list[dict[str, Any]],
) -> None:
    """The graph should pause with a tool_approval payload when read_file is requested."""
    from app.services.agent_service import invoke

    _build_graph(
        monkeypatch,
        content="I will read a file.",
        tool_calls=[
            {
                "id": "call_1",
                "name": "read_file",
                "args": {"path": "/tmp/test.txt"},
            }
        ],
    )

    result = await invoke(
        user_message="读取文件",
        user_id="test",
        thread_id="test-tool-hil",
    )

    assert result.get("pending_approval")
    assert result["pending_approval"]["type"] == "tool_approval"
    assert result["pending_approval"]["tool_calls"][0]["name"] == "read_file"
    assert not fake_read_file


@pytest.mark.anyio
async def test_tool_review_approved_executes_read_file(
    monkeypatch: pytest.MonkeyPatch,
    fake_read_file: list[dict[str, Any]],
) -> None:
    """Approving the HIL review should let read_file run and return its output."""
    from app.services.agent_service import invoke

    _build_graph(
        monkeypatch,
        content="File content received.",
        tool_calls=[
            {
                "id": "call_1",
                "name": "read_file",
                "args": {"path": "/tmp/test.txt"},
            }
        ],
    )

    thread_id = "test-tool-approved"
    first = await invoke(
        user_message="读取文件",
        user_id="test",
        thread_id=thread_id,
    )
    assert first.get("pending_approval")

    resumed = await invoke(
        user_message="",
        thread_id=thread_id,
        resume={"approved": True},
    )

    assert len(fake_read_file) == 1
    assert fake_read_file[0]["path"] == "/tmp/test.txt"
    assert "content" in resumed["message"]["content"].lower()


@pytest.mark.anyio
async def test_tool_review_denied_cancels_read_file(
    monkeypatch: pytest.MonkeyPatch,
    fake_read_file: list[dict[str, Any]],
) -> None:
    """Denying the HIL review should cancel read_file and let the agent respond."""
    from app.services.agent_service import invoke

    _build_graph(
        monkeypatch,
        content="用户拒绝了读取文件请求，我无法访问该文件。",
        tool_calls=[
            {
                "id": "call_1",
                "name": "read_file",
                "args": {"path": "/tmp/test.txt"},
            }
        ],
    )

    thread_id = "test-tool-denied"
    first = await invoke(
        user_message="读取文件",
        user_id="test",
        thread_id=thread_id,
    )
    assert first.get("pending_approval")

    resumed = await invoke(
        user_message="",
        thread_id=thread_id,
        resume={"approved": False},
    )

    assert not fake_read_file
    assert "拒绝" in resumed["message"]["content"] or "无法" in resumed["message"]["content"]


@pytest.mark.anyio
async def test_non_sensitive_tool_runs_without_review(
    monkeypatch: pytest.MonkeyPatch,
    fake_read_file: list[dict[str, Any]],
) -> None:
    """A benign tool like calculator should execute without HIL interruption."""
    from app.services.agent_service import invoke

    _build_graph(
        monkeypatch,
        content="The answer is 42.",
        tool_calls=[
            {
                "id": "call_1",
                "name": "calculator",
                "args": {"expression": "6*7"},
            }
        ],
    )

    result = await invoke(
        user_message="计算 6*7",
        user_id="test",
        thread_id="test-calc",
    )

    assert not result.get("pending_approval")
    assert "42" in result["message"]["content"]


__all__ = []
