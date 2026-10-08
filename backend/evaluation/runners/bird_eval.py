"""Run a BIRD-SQL NL2SQL evaluation.

Example:
    cd backend
    .venv/bin/python -m evaluation.runners.bird_eval \
        evaluation/datasets/bird_mini_dev.jsonl \
        -o /tmp/bird_eval_report.json

The runner asks the agent to generate SQL via the ``execute_sql`` tool,
extracts the generated SQL from the tool call, and then compares the gold SQL
with the generated SQL to compute execution accuracy.

- If ``EVAL_STARROCKS_DATABASE`` is unset, both queries run against the
  original SQLite database shipped with the dataset.
- If ``EVAL_STARROCKS_DATABASE`` is set, the gold SQL runs on SQLite while the
  generated SQL runs on the configured StarRocks database (hybrid mode).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from app.agent.prompts import build_system_prompt
from app.bootstrap import bootstrap_agent, shutdown_agent
from app.config import get_settings
from app.services.agent_service import invoke
from app.tools import get_llm_tools
from evaluation.metrics.sql_judges import judge_sql_execution, judge_sql_execution_hybrid


def _build_system_prefix(target_db: str, *, use_dosi: bool) -> str:
    if use_dosi:
        return f"""你正在参与 BIRD-SQL 评测。
你的任务是把用户的问题翻译成一个 SQL 查询。

请优先使用 DoSI 工具（如 `dosi_list_metrics`、`dosi_list_dimensions`、`dosi_compile_sql`）基于语义层生成 {target_db} SQL；
生成的 SQL 必须通过 `execute_sql` 执行。严禁使用 `dosi_run_query` 直接取数。
如果 DoSI 无法生成符合要求的查询，再回退到 `execute_sql` 直接写出 {target_db} SQL。
评测中禁止使用 `calculator`，所有计算必须在 SQL 中完成。
即使执行环境报错，你提供的 SQL 也会被捕获并评分，所以请专注于写出正确的查询。
"""
    return f"""你正在参与 BIRD-SQL 评测。
你的任务是把用户的问题翻译成一个 SQL 查询。

你必须通过调用 `execute_sql` 工具来生成 SQL。
即使执行环境报错，你提供的 SQL 也会被捕获并评分，所以请专注于写出正确的 {target_db} 查询。
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
    """Extract the last SQL from the agent's tool calls, if present.

    Scoring uses the final SQL (and its result values) rather than the first
    one, so intermediate probe queries do not override the agent's final answer.
    """
    tool_calls = result.get("tool_calls", [])
    for call in reversed(tool_calls):
        name = call.get("name") or ""
        if name != "execute_sql":
            continue
        args = call.get("args") or call.get("input") or {}
        sql = args.get("sql") or args.get("SQL") or ""
        if isinstance(sql, str):
            return sql.strip()
    return ""


def _build_user_message(case: dict[str, Any]) -> str:
    return f"问题：{case['question']}"


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run a single BIRD case and score it by execution accuracy."""
    settings = get_settings()
    question = case["question"]
    db_path = case.get("db_path", "")
    gold_sql = case.get("expected_args", {}).get("sql", "")

    # When a StarRocks evaluation database is configured, the agent is expected
    # to produce StarRocks-native SQL and we compare gold (SQLite) vs predicted
    # (StarRocks). Otherwise both queries run on the original SQLite DB.
    is_hybrid = bool(settings.eval_starrocks_database)
    target_db = "StarRocks" if is_hybrid else "SQLite"

    # If DoSI MCP is enabled, ask the model to try the semantic layer first.
    use_dosi = bool(settings.dosi_mcp_enabled)

    user_message = _build_user_message(case)

    # Compose the full evaluation system prompt: base rules first, then the
    # BIRD-specific task instruction, so both runners behave consistently.
    system_prompt = (
        f"{build_system_prompt(get_llm_tools())}\n\n"
        f"{_build_system_prefix(target_db, use_dosi=use_dosi)}"
    )

    result = await invoke(
        user_message=user_message,
        user_id="bird_eval",
        system_prompt=system_prompt,
    )
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

    if is_hybrid:
        passed, reason = await judge_sql_execution_hybrid(
            gold_sql,
            generated_sql,
            db_path,
            host=settings.eval_starrocks_host,
            port=settings.eval_starrocks_port,
            user=settings.eval_starrocks_user,
            password=settings.eval_starrocks_password,
            database=settings.eval_starrocks_database,
        )
    else:
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


async def _main(args: argparse.Namespace) -> int:
    """Bootstrap the agent, run the evaluation, and tear down."""
    if args.no_mcp:
        settings = get_settings()
        settings.mcp_enabled = False
        settings.dosi_mcp_enabled = False

    await bootstrap_agent(enable_mcp=not args.no_mcp)
    try:
        await run_eval(args.dataset, args.output, args.max_concurrent)
        return 0
    finally:
        await shutdown_agent()


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
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Disable MCP tools and use only built-in tools",
    )
    args = parser.parse_args()

    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
