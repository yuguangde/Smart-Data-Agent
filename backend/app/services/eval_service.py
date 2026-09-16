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
from app.tools.knowledge_search import _query as _search_knowledge
from evaluation.metrics.sql_judges import judge_sql_execution
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


def _extract_tool_sql(tool_calls: list[dict[str, Any]]) -> str:
    """Return SQL from an execute_sql tool call, if present."""
    for call in tool_calls:
        name = call.get("name") or ""
        if name != "execute_sql":
            continue
        args = call.get("args") or call.get("input") or {}
        sql = args.get("sql") or args.get("SQL") or ""
        if isinstance(sql, str):
            return sql.strip()
    return ""


def _build_bird_message(case: dict[str, Any], retrieved_context: str = "") -> str:
    """Build a prompt that asks the agent to generate SQL for a BIRD case."""
    schema = case.get("schema", "")
    question = case.get("question", "")
    context_section = (
        "\n\nAdditional semantic guidance (retrieved from knowledge base):\n"
        f"{retrieved_context}\n"
        if retrieved_context
        else ""
    )
    return (
        "You are participating in an NL2SQL benchmark evaluation.\n"
        "Your task is to translate the user's question into a single SQL query.\n"
        "Use the provided database schema to understand the tables and columns.\n"
        "You must generate the SQL by calling the execute_sql tool.\n"
        "Even if the execution environment reports an error, the SQL you provided\n"
        "will be captured and evaluated, so focus on producing a correct query.\n\n"
        f"Database schema:\n{schema}{context_section}\n\n"
        f"Question: {question}\n\n"
        "Please generate the SQL by calling the execute_sql tool. "
        "Do not include explanations, only the SQL."
    )


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run a single case to completion and score it.

    Supports two evaluation modes:
    - BIRD-SQL mode: case contains ``schema`` and ``db_path``; the agent is
      asked to generate SQL, which is executed against a local SQLite DB.
    - General mode: case contains ``expected_tool`` / ``expected_in_answer``;
      existing tool/relevance judges are applied.

    If the agent pauses for HITL approval, the runner automatically approves
    the pending tool call so that tool-execution correctness can be measured.
    """
    question = case["question"]
    expected_tool = case.get("expected_tool")
    expected_args = case.get("expected_args", {})
    expected_keywords = case.get("expected_in_answer", [])
    schema = case.get("schema")
    db_path = case.get("db_path")

    settings = get_settings()
    retrieved_context = ""
    if schema and db_path and settings.eval_retrieval_enabled:
        query = f"{question}\n\n{schema}"
        # Pick the semantic-layer file that matches the current DB so we do not
        # mix debit_card and student_club guidance.
        db_name = Path(db_path).stem
        filename_filter = f"bird-semantic-layer-{db_name}.md"
        retrieved_context = _search_knowledge(
            query,
            settings.eval_retrieval_top_k,
            filename_filter=filename_filter,
        )

    user_message = (
        _build_bird_message(case, retrieved_context=retrieved_context)
        if schema
        else question
    )
    result = await invoke(user_message=user_message, user_id="eval")

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

    # BIRD-SQL mode: judge by executing generated SQL vs gold SQL.
    if schema and db_path:
        generated_sql = _extract_tool_sql(tool_calls)
        gold_sql = expected_args.get("sql", "") if expected_args else ""
        if not generated_sql:
            scores["execution_accuracy"] = {
                "passed": False,
                "reason": "Agent did not call execute_sql tool",
            }
        else:
            passed, reason = await judge_sql_execution(gold_sql, generated_sql, db_path)
            scores["execution_accuracy"] = {"passed": passed, "reason": reason}
        return {
            "question": question,
            "thread_id": result["thread_id"],
            "answer": answer,
            "generated_sql": generated_sql,
            "gold_sql": gold_sql,
            "tool_calls": tool_calls,
            "scores": scores,
        }

    # General mode: existing tool/relevance judges.
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

    # Detect evaluation mode from the first case to pick the right metrics.
    first_case = cases[0] if cases else {}
    is_bird = bool(first_case.get("schema") and first_case.get("db_path"))

    total = len(cases)
    await store.update_run_progress(
        run_id=run_id,
        processed=0,
        passed=0,
        errored=0,
        metrics={
            "accuracy": 0.0,
            "tool_call_accuracy": None if not is_bird else 0.0,
            "relevance_accuracy": None if not is_bird else 0.0,
            "execution_accuracy": 0.0 if is_bird else None,
        },
    )

    processed = 0
    passed = 0
    errored = 0
    tool_passed = 0
    tool_total = 0
    relevance_passed = 0
    relevance_total = 0
    execution_passed = 0
    execution_total = 0

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
            if "execution_accuracy" in result["scores"]:
                execution_total += 1
                if result["scores"]["execution_accuracy"]["passed"]:
                    execution_passed += 1

            await store.save_result(
                run_id=run_id,
                case_index=idx,
                question=result["question"],
                passed=case_passed,
                scores=result["scores"],
                answer=result.get("generated_sql", result["answer"]),
                generated_sql=result.get("generated_sql"),
                gold_sql=result.get("gold_sql"),
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
                generated_sql=None,
                gold_sql=None,
                error=str(exc),
            )

        metrics: dict[str, Any] = {"accuracy": passed / processed if processed else 0.0}
        if tool_total:
            metrics["tool_call_accuracy"] = tool_passed / tool_total
        if relevance_total:
            metrics["relevance_accuracy"] = relevance_passed / relevance_total
        if execution_total:
            metrics["execution_accuracy"] = execution_passed / execution_total

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
