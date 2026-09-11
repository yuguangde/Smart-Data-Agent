"""Run a BIRD-SQL NL2SQL evaluation.

Example:
    cd backend
    .venv/bin/python -m evaluation.runners.bird_eval \
        evaluation/datasets/bird_mini_dev.jsonl \
        -o /tmp/bird_eval_report.json

The runner works in SQL-only mode: it asks the agent to generate SQL via the
``execute_sql`` tool, extracts the generated SQL from the tool call, and then
executes both the gold and generated SQL against the original SQLite database
to compute execution accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from app.services.agent_service import invoke
from evaluation.metrics.sql_judges import judge_sql_execution


SYSTEM_PREFIX = """You are participating in an NL2SQL benchmark evaluation.
Your task is to translate the user's question into a single SQL query.

Use the provided database schema to understand the tables and columns.
You must generate the SQL by calling the `execute_sql` tool.
Even if the execution environment reports an error, the SQL you provided will
be captured and evaluated, so focus on producing a correct SQLite query.

Database schema:
{schema}
"""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def _extract_generated_sql(result: dict[str, Any]) -> str:
    """Extract SQL from the agent's execute_sql tool call, if present."""
    tool_calls = result.get("tool_calls", [])
    for call in tool_calls:
        name = call.get("name") or ""
        if name != "execute_sql":
            continue
        args = call.get("args") or call.get("input") or {}
        sql = args.get("sql") or args.get("SQL") or ""
        if isinstance(sql, str):
            return sql.strip()
    return ""


def _build_user_message(case: dict[str, Any]) -> str:
    return (
        f"Question: {case['question']}\n\n"
        "Please generate the SQL by calling the execute_sql tool. "
        "Do not include explanations, only the SQL."
    )


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run a single BIRD case and score it by execution accuracy."""
    question = case["question"]
    schema = case.get("schema", "")
    db_path = case.get("db_path", "")
    gold_sql = case.get("expected_args", {}).get("sql", "")

    system_prompt = SYSTEM_PREFIX.format(schema=schema)
    user_message = _build_user_message(case)

    # Combine system prompt and user message so the agent receives schema context.
    full_message = f"{system_prompt}\n\n{user_message}"

    result = await invoke(user_message=full_message, user_id="bird_eval")
    generated_sql = _extract_generated_sql(result)

    if not generated_sql:
        return {
            "index": case.get("index", 0),
            "question": question,
            "gold_sql": gold_sql,
            "generated_sql": generated_sql,
            "answer": result.get("message", {}).get("content", ""),
            "tool_calls": result.get("tool_calls", []),
            "passed": False,
            "scores": {
                "execution_accuracy": {
                    "passed": False,
                    "reason": "Agent did not call execute_sql tool",
                }
            },
        }

    if not db_path or not Path(db_path).exists():
        return {
            "index": case.get("index", 0),
            "question": question,
            "gold_sql": gold_sql,
            "generated_sql": generated_sql,
            "passed": False,
            "scores": {
                "execution_accuracy": {
                    "passed": False,
                    "reason": f"Database not found: {db_path}",
                }
            },
        }

    passed, reason = await judge_sql_execution(gold_sql, generated_sql, db_path)
    return {
        "index": case.get("index", 0),
        "question": question,
        "gold_sql": gold_sql,
        "generated_sql": generated_sql,
        "passed": passed,
        "scores": {
            "execution_accuracy": {"passed": passed, "reason": reason}
        },
    }


async def run_eval(
    dataset_path: Path,
    out: TextIO,
    max_concurrent: int = 3,
) -> None:
    """Evaluate all cases in a BIRD JSONL dataset and write a report."""
    cases = _load_jsonl(dataset_path)

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_with_limit(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            print(f"[{case.get('index', 0)}/{len(cases)}] {case.get('question', '')[:60]}...", file=sys.stderr)
            try:
                return await run_case(case)
            except Exception as exc:
                return {
                    "index": case.get("index", 0),
                    "question": case.get("question", ""),
                    "error": str(exc),
                    "passed": False,
                    "scores": {
                        "execution_accuracy": {
                            "passed": False,
                            "reason": f"Runner error: {exc}",
                        }
                    },
                }

    results = await asyncio.gather(*(_run_with_limit(c) for c in cases))

    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    errored = sum(1 for r in results if "error" in r)

    report = {
        "dataset": str(dataset_path),
        "total": total,
        "passed": passed,
        "errored": errored,
        "execution_accuracy": passed / total if total else 0.0,
        "results": results,
    }
    json.dump(report, out, ensure_ascii=False, indent=2, default=str)
    out.write("\n")

    print(
        f"Execution accuracy: {passed}/{total} = {report['execution_accuracy']:.2%}",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate DataAgent on BIRD-SQL")
    parser.add_argument(
        "dataset",
        type=Path,
        default=Path("evaluation/datasets/bird_mini_dev.jsonl"),
        nargs="?",
        help="Path to the BIRD evaluation JSONL file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=argparse.FileType("w", encoding="utf-8"),
        default=sys.stdout,
        help="Output file for the report (default: stdout)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=3,
        help="Maximum concurrent agent invocations (default: 3)",
    )
    args = parser.parse_args()

    asyncio.run(run_eval(args.dataset, args.output, args.max_concurrent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
