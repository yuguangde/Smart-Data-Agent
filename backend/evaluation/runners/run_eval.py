"""Run a simple evaluation over a JSONL dataset.

Example:
    cd backend
    .venv/bin/python -m evaluation.runners.run_eval \
        evaluation/datasets/read_file_cases.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from evaluation.metrics.tool_judges import (
    judge_answer_relevance,
    judge_read_file_call,
)
from app.services.agent_service import invoke


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run a single case to completion and score it.

    If the agent pauses for HITL approval, the runner automatically approves
    the pending tool call so that tool-execution correctness can be measured.
    """
    question = case["question"]
    expected_tool = case.get("expected_tool")
    expected_args = case.get("expected_args", {})
    expected_keywords = case.get("expected_in_answer", [])

    result = await invoke(user_message=question, user_id="eval")
    approval_triggered = bool(result.get("pending_approval"))

    if approval_triggered:
        result = await invoke(
            user_message="",
            thread_id=result["thread_id"],
            user_id="eval",
            resume={"approved": True},
        )

    tool_calls = result.get("tool_calls", [])
    answer = result.get("message", {}).get("content", "")

    scores: dict[str, Any] = {
        "approval_triggered": approval_triggered,
    }

    if expected_tool:
        passed, reason = judge_read_file_call(tool_calls, expected_args)
        scores["tool_call"] = {"passed": passed, "reason": reason}

    if expected_keywords:
        passed, reason = judge_answer_relevance(answer, expected_keywords)
        scores["relevance"] = {"passed": passed, "reason": reason}

    return {
        "question": question,
        "thread_id": result["thread_id"],
        "approval_triggered": approval_triggered,
        "tool_calls": tool_calls,
        "answer": answer,
        "scores": scores,
    }


async def run_eval(dataset_path: Path, out: TextIO) -> None:
    """Load cases from ``dataset_path``, evaluate, and write a JSON report."""
    cases: list[dict[str, Any]] = []
    with dataset_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))

    results: list[dict[str, Any]] = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['question'][:60]}...", file=sys.stderr)
        try:
            results.append(await run_case(case))
        except Exception as exc:  # pragma: no cover - defensive
            results.append(
                {
                    "question": case.get("question", ""),
                    "error": str(exc),
                }
            )

    total = len(results)
    approval_hits = sum(1 for r in results if r.get("approval_triggered"))
    tool_passed = sum(
        1 for r in results
        if r.get("scores", {}).get("tool_call", {}).get("passed")
    )
    relevance_passed = sum(
        1 for r in results
        if r.get("scores", {}).get("relevance", {}).get("passed")
    )

    report = {
        "dataset": str(dataset_path),
        "total": total,
        "approval_triggered": approval_hits,
        "tool_call_accuracy": tool_passed / total if total else 0.0,
        "relevance_accuracy": relevance_passed / total if total else 0.0,
        "results": results,
    }

    json.dump(report, out, ensure_ascii=False, indent=2, default=str)
    out.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate agent on a dataset")
    parser.add_argument(
        "dataset",
        type=Path,
        default=Path("evaluation/datasets/read_file_cases.jsonl"),
        nargs="?",
        help="Path to the JSONL dataset",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=argparse.FileType("w", encoding="utf-8"),
        default=sys.stdout,
        help="Output file for the report (default: stdout)",
    )
    args = parser.parse_args()

    asyncio.run(run_eval(args.dataset, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
