"""Evaluation execution service.

Wraps the existing offline runner logic so it can be invoked through the HTTP
API and persisted in EvalStore.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.memory.eval_store import EvalStore
from app.services.agent_service import invoke
from evaluation.metrics.tool_judges import (
    judge_answer_relevance,
    judge_read_file_call,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, int, dict[str, Any]], None]


def resolve_dataset(name: str) -> Path:
    """Return the absolute path to a dataset file, guarding against traversal.

    Raises:
        ValueError: if the name is empty, contains path separators, or resolves
            outside the configured datasets directory.
        FileNotFoundError: if the file does not exist.
    """
    if not name:
        raise ValueError("Dataset name is required")
    if "/" in name or "\\" in name or ".." in name.split("/"):
        raise ValueError(f"Invalid dataset name: {name!r}")

    settings = get_settings()
    datasets_dir = settings.eval_datasets_dir_resolved
    path = datasets_dir / name
    if not path.is_relative_to(datasets_dir):
        raise ValueError(f"Dataset path escapes datasets directory: {name!r}")
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {name!r}")
    if not path.is_file():
        raise ValueError(f"Dataset is not a file: {name!r}")
    return path


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load cases from a JSONL file."""
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


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

    # Auto-approve HITL pauses so the runner can observe tool execution.
    max_approvals = 8
    approvals = 0
    while result.get("pending_approval") and approvals < max_approvals:
        result = await invoke(
            user_message="",
            thread_id=result["thread_id"],
            user_id="eval",
            resume={"approved": True},
        )
        approvals += 1

    tool_calls = result.get("tool_calls", [])
    answer = result.get("message", {}).get("content", "")

    scores: dict[str, Any] = {}

    if expected_tool:
        passed, reason = judge_read_file_call(tool_calls, expected_args)
        scores["tool_call"] = {"passed": passed, "reason": reason}

    if expected_keywords:
        passed, reason = judge_answer_relevance(answer, expected_keywords)
        scores["relevance"] = {"passed": passed, "reason": reason}

    return {
        "question": question,
        "thread_id": result["thread_id"],
        "answer": answer,
        "tool_calls": tool_calls,
        "scores": scores,
    }


def _case_passed(scores: dict[str, Any]) -> bool:
    """Return True when every scored metric passed."""
    if not scores:
        return True
    return all(bool(metric.get("passed")) for metric in scores.values())


async def run_eval(
    run_id: str,
    dataset_path: Path,
    store: EvalStore,
    progress_cb: ProgressCallback | None = None,
) -> None:
    """Execute all cases in a dataset and persist results.

    This function is designed to be run as a background task. It updates the
    run row as it progresses and marks it completed/failed at the end.
    """
    try:
        cases = load_jsonl(dataset_path)
    except Exception as exc:
        logger.exception("Failed to load dataset for run %s: %s", run_id, exc)
        await store.finalize_run(run_id=run_id, status="failed", error=str(exc))
        return

    total = len(cases)
    await store.update_run_progress(
        run_id=run_id,
        processed=0,
        passed=0,
        errored=0,
        metrics={"accuracy": 0.0, "tool_call_accuracy": None, "relevance_accuracy": None},
    )

    processed = 0
    passed = 0
    errored = 0
    tool_passed = 0
    tool_total = 0
    relevance_passed = 0
    relevance_total = 0

    for idx, case in enumerate(cases, start=1):
        try:
            result = await run_case(case)
            case_passed = _case_passed(result["scores"])
            if case_passed:
                passed += 1
            processed += 1

            if "tool_call" in result["scores"]:
                tool_total += 1
                if result["scores"]["tool_call"]["passed"]:
                    tool_passed += 1
            if "relevance" in result["scores"]:
                relevance_total += 1
                if result["scores"]["relevance"]["passed"]:
                    relevance_passed += 1

            await store.save_result(
                run_id=run_id,
                case_index=idx,
                question=result["question"],
                passed=case_passed,
                scores=result["scores"],
                answer=result["answer"],
                error=None,
            )
        except Exception as exc:
            logger.exception("Case %d failed in run %s: %s", idx, run_id, exc)
            errored += 1
            processed += 1
            await store.save_result(
                run_id=run_id,
                case_index=idx,
                question=case.get("question", ""),
                passed=False,
                scores={},
                answer="",
                error=str(exc),
            )

        metrics: dict[str, Any] = {"accuracy": passed / processed if processed else 0.0}
        if tool_total:
            metrics["tool_call_accuracy"] = tool_passed / tool_total
        if relevance_total:
            metrics["relevance_accuracy"] = relevance_passed / relevance_total

        await store.update_run_progress(
            run_id=run_id,
            processed=processed,
            passed=passed,
            errored=errored,
            metrics=metrics,
        )
        if progress_cb:
            progress_cb(processed, passed, errored, metrics)

    await store.finalize_run(run_id=run_id, status="completed")


__all__ = ["resolve_dataset", "load_jsonl", "run_case", "run_eval"]
