"""Simple deterministic judges for tool-calling correctness."""
from __future__ import annotations

from typing import Any


def judge_read_file_call(
    tool_calls: list[dict[str, Any]],
    expected_args: dict[str, Any],
) -> tuple[bool, str]:
    """Check whether the agent invoked ``read_file`` with the expected path.

    Returns ``(passed, reason)``.
    """
    if not tool_calls:
        return False, "No tool calls were produced"

    for call in tool_calls:
        if call.get("name") != "read_file":
            continue
        input_args = call.get("args") or call.get("input") or {}
        actual_path = input_args.get("path")
        expected_path = expected_args.get("path")
        if actual_path == expected_path:
            return True, f"read_file called with path={actual_path!r}"
        return False, (
            f"read_file called with wrong path: "
            f"expected {expected_path!r}, got {actual_path!r}"
        )

    return False, f"Expected read_file, got {[c.get('name') for c in tool_calls]}"


def judge_answer_relevance(answer: str, expected_keywords: list[str]) -> tuple[bool, str]:
    """Pass if all expected keywords appear in the final answer (case-insensitive)."""
    lower = answer.lower()
    missing = [kw for kw in expected_keywords if kw.lower() not in lower]
    if missing:
        return False, f"Missing keywords: {missing}"
    return True, f"All keywords present: {expected_keywords}"
