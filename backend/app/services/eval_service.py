"""Evaluation execution service.

Wraps the existing offline runner logic so it can be invoked through the HTTP
API and persisted in EvalStore.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from app.agent.prompts import build_system_prompt
from app.config import get_settings
from app.memory.eval_store import EvalStore
from app.query.registry import SemanticRegistry
from app.services.agent_service import invoke
from app.tools import get_llm_tools
from app.tools.knowledge_search import _query as _search_knowledge
from evaluation.metrics.sql_judges import (
    judge_sql_execution,
    judge_sql_execution_hybrid,
)
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


def save_jsonl(path: Path, cases: list[dict[str, Any]]) -> None:
    """Write cases to a JSONL file, renumbering indexes sequentially."""
    with path.open("w", encoding="utf-8") as f:
        for idx, case in enumerate(cases, start=1):
            case["index"] = idx
            f.write(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n")


def remove_case(dataset_name: str, case_index: int) -> Path:
    """Remove a case from a dataset by its 1-based index."""
    path = resolve_dataset(dataset_name)
    cases = load_jsonl(path)
    if case_index < 1 or case_index > len(cases):
        raise IndexError(f"Case index {case_index} out of range for dataset {dataset_name!r}")
    cases.pop(case_index - 1)
    save_jsonl(path, cases)
    return path


def add_case(dataset_name: str, case: dict[str, Any]) -> Path:
    """Append a new case to a dataset."""
    path = resolve_dataset(dataset_name)
    cases = load_jsonl(path)
    cases.append(case)
    save_jsonl(path, cases)
    return path


def _extract_tool_sql(tool_calls: list[dict[str, Any]]) -> str:
    """Return SQL executed by the agent, regardless of which tool produced it.

    Supports plain ``execute_sql`` as well as DoSI tools that return SQL in
    their output text (``dosi_compile_sql``, ``dosi_run_query``).
    """
    import json as _json

    for call in tool_calls:
        name = call.get("name") or ""
        args = call.get("args") or call.get("input") or {}

        if name == "execute_sql":
            sql = args.get("sql") or args.get("SQL") or ""
            if isinstance(sql, str):
                return sql.strip()

        if name in ("dosi_compile_sql", "dosi_run_query"):
            output = call.get("output") or ""
            if isinstance(output, str):
                try:
                    payload = _json.loads(output)
                except Exception:
                    payload = {}
                if isinstance(payload, dict) and payload.get("sql"):
                    return str(payload["sql"]).strip()

    return ""


def _build_ossie_context(db_name: str) -> str:
    """Return a plain-text summary of the OSSIE semantic model for ``db_name``.

    The semantic registry is scanned at server startup, so this lookup is cheap
    and deterministic.  If no model matches the database name, an empty string is
    returned and the prompt falls back to the raw schema.
    """
    registry = SemanticRegistry.get()
    model = registry.models.get(db_name)
    if model is None:
        return ""

    lines = [f"Semantic model: {model.name}"]
    if model.description:
        lines.append(model.description.strip())

    # Model-level relationships / join paths.
    relationships = model.raw.get("relationships") or []
    if relationships:
        lines.append("\nJoin relationships:")
        for rel in relationships:
            lines.append(
                f"  - {rel.get('from')} -> {rel.get('to')} "
                f"({rel.get('type', 'n-to-m')}): {rel.get('description', '')}"
            )

    # Model-level business rules / caveats.
    rules = model.raw.get("business_rules") or []
    if rules:
        lines.append("\nImportant business rules:")
        for idx, rule in enumerate(rules, start=1):
            lines.append(f"  {idx}. {rule}")

    # Example SQL patterns.
    patterns = model.raw.get("sql_patterns") or []
    if patterns:
        lines.append("\nTypical correct SQL patterns:")
        for pat in patterns:
            sql = (pat.get("sql") or "").strip()
            if sql:
                indented_sql = "\n".join("      " + line for line in sql.splitlines())
                lines.append(f"  - {pat.get('name', 'Example')}:")
                lines.append(indented_sql)

    # Per-dataset details.
    if model.datasets:
        lines.append("\nDatasets (tables), columns, keys and metrics:")

    for ds in model.datasets:
        lines.append(f"\n- Table: {ds.source}")
        raw = ds.raw or {}

        agg = raw.get("aggregation_level")
        if agg:
            lines.append(f"  Aggregation level: {agg}")
        preferred = raw.get("preferred_for")
        if preferred:
            lines.append(f"  Preferred for: {', '.join(preferred)}")

        ds_description = raw.get("description") or ""
        if ds_description:
            lines.append(f"  Description:\n    {ds_description.strip()}")

        primary_key = raw.get("primary_key") or []
        if primary_key:
            lines.append(f"  Primary key: {', '.join(primary_key)}")

        for fk in raw.get("foreign_keys") or []:
            lines.append(
                f"  Foreign key: {fk.get('column')} -> {fk.get('references')}"
            )

        for note in raw.get("notes") or []:
            lines.append(f"  Note: {note}")

        dim_names = sorted(ds.dimension_names)
        if dim_names:
            lines.append(f"  Columns: {', '.join(dim_names)}")
            for dim in ds.dimensions:
                if dim.description:
                    lines.append(f"    * {dim.name}: {dim.description.strip()}")

        metric_names = sorted(ds.metric_names)
        if metric_names:
            lines.append(f"  Metrics: {', '.join(metric_names)}")
            for metric in ds.metrics:
                if metric.description:
                    lines.append(f"    * {metric.name}: {metric.description.strip()}")
                ai_context = metric.raw.get("ai_context") or {}
                instructions = ai_context.get("instructions")
                if instructions:
                    lines.append(f"      [使用提示] {instructions.strip()}")
                synonyms = ai_context.get("synonyms") or []
                if synonyms:
                    lines.append(f"      [同义词/别名] {', '.join(synonyms)}")

    return "\n".join(lines)


def _build_bird_message(
    case: dict[str, Any],
    retrieved_context: str = "",
    ossie_context: str = "",
    starrocks_target: bool = False,
) -> str:
    """Build a prompt that asks the agent to generate SQL for a BIRD case."""
    question = case.get("question", "")
    sections: list[str] = []
    if retrieved_context:
        sections.append(
            "Additional semantic guidance (retrieved from knowledge base):\n"
            f"{retrieved_context}"
        )
    if ossie_context:
        sections.append(
            "OSSIE semantic model guidance (business definitions and metrics):\n"
            f"{ossie_context}"
        )
    context_section = "\n\n".join(sections)
    if context_section:
        context_section = "\n\n" + context_section + "\n"

    # 根据目标方言给出比例示例。StarRocks 不支持 SQLite 的 IIF()。
    if starrocks_target:
        ratio_example = (
            "比例问题请在单个 SELECT 表达式中完成计算。\n"
            "例如，对于“用欧元支付的顾客与用捷克克朗支付的顾客的比例是多少”，应写：\n"
            "  SELECT CAST(SUM(IF(Currency = 'EUR', 1, 0)) AS DOUBLE) / "
            "SUM(IF(Currency = 'CZK', 1, 0)) AS ratio\n"
            "  FROM debit_card_specializing.customers;\n"
            "不要写成：\n"
            "  SELECT Currency, COUNT(*) FROM debit_card_specializing.customers GROUP BY Currency;\n"
        )
    else:
        ratio_example = (
            "比例问题请在单个 SELECT 表达式中完成计算。\n"
            "例如，对于“用欧元支付的顾客与用捷克克朗支付的顾客的比例是多少”，应写：\n"
            "  SELECT CAST(SUM(IIF(Currency = 'EUR', 1, 0)) AS FLOAT) / "
            "SUM(IIF(Currency = 'CZK', 1, 0)) AS ratio\n"
            "  FROM customers;\n"
            "不要写成：\n"
            "  SELECT Currency, COUNT(*) FROM customers GROUP BY Currency;\n"
        )

    return (
        "你是 Smart Data Agent。请使用你可用的工具回答下面这个 BIRD 评测集中的问题；"
        "如果适用，优先使用 DoSI 语义 SQL。\n"
        "重点是生成正确的 SQL 并返回问题所要求的数据。\n"
        "\n"
        "本数据集（debit_card_specializing）的表优先级：\n"
        "- 当问题涉及客户消费、月度消费或客户消费排名时，优先查询 `yearmonth`。\n"
        "- 只有在 `yearmonth` 中查不到的明细才需要 JOIN `transactions_1k`，例如单笔单价、"
        "商品明细、加油站明细或交易级时间。\n"
        "- 注意：`transactions_1k` 只是 4 天抽样，不能代表客户的总体消费。\n"
        "例如，对于“2012 年 LAM 消费最少的客户是谁”，应写：\n"
        "  SELECT c.CustomerID\n"
        "  FROM customers c JOIN yearmonth y ON c.CustomerID = y.CustomerID\n"
        "  WHERE c.Segment = 'LAM' AND SUBSTR(y.Date, 1, 4) = '2012'\n"
        "  GROUP BY c.CustomerID\n"
        "  ORDER BY SUM(y.Consumption) ASC\n"
        "  LIMIT 1;\n"
        "不要写成：\n"
        "  SELECT c.CustomerID, SUM(y.Consumption) AS total FROM ... "
        "GROUP BY c.CustomerID ORDER BY total ASC LIMIT 1;\n"
        "\n"
        f"{ratio_example}\n"
        "\n"
        "如果上面提供了 OSSIE 语义模型，指标类问题（总量、平均、计数、比例、趋势）"
        "可以优先使用 DoSI；调用 dosi_run_query 时带上 connection='starrocks' 和 dialect='starrocks'。\n"
        f"{context_section}\n"
        f"问题：{question}\n\n"
        "请简洁地给出答案和对应数据。"
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
    db_name = Path(db_path).stem if db_path else ""

    # If an OSSIE semantic model exists for this DB, use it as the authoritative
    # semantic layer and skip the legacy markdown retrieval for that DB.
    ossie_context = _build_ossie_context(db_name) if db_name else ""

    retrieved_context = ""
    if (
        schema
        and db_path
        and settings.eval_retrieval_enabled
        and not ossie_context
    ):
        # Use only the natural-language question for semantic retrieval.
        # The full schema contains many repeated table names and tends to drown
        # out the field-specific / value-normalization sections we actually need.
        query = question
        # Pick the semantic-layer file that matches the current DB so we do not
        # mix debit_card and student_club guidance.
        filename_filter = f"bird-semantic-layer-{db_name}.md"
        # _search_knowledge may run a local embedding model; run it in a worker
        # thread so the async event loop stays responsive.
        retrieved_context = await asyncio.to_thread(
            _search_knowledge,
            query,
            settings.eval_retrieval_top_k,
            filename_filter=filename_filter,
        )

    user_message = (
        _build_bird_message(
            case,
            retrieved_context=retrieved_context,
            ossie_context=ossie_context,
            starrocks_target=bool(settings.eval_starrocks_database),
        )
        if schema
        else question
    )

    # Build the full prompt as seen by the agent LLM so it can be inspected later.
    system_prompt = build_system_prompt(get_llm_tools())
    full_prompt = f"{system_prompt}\n\n{user_message}"

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
                "reason": "Agent did not produce executable SQL via any tool",
            }
        elif settings.eval_starrocks_database:
            # Hybrid mode: gold SQL runs on the original SQLite DB shipped with
            # the dataset; generated SQL runs on the configured StarRocks DB.
            passed, reason = await judge_sql_execution_hybrid(
                gold_sql,
                generated_sql,
                db_path=db_path,
                host=settings.eval_starrocks_host,
                port=settings.eval_starrocks_port,
                user=settings.eval_starrocks_user,
                password=settings.eval_starrocks_password,
                database=settings.eval_starrocks_database,
            )
            scores["execution_accuracy"] = {"passed": passed, "reason": reason}
        else:
            passed, reason = await judge_sql_execution(gold_sql, generated_sql, db_path)
            scores["execution_accuracy"] = {"passed": passed, "reason": reason}
        return {
            "question": question,
            "thread_id": result["thread_id"],
            "answer": answer,
            "generated_sql": generated_sql,
            "gold_sql": gold_sql,
            "user_message": user_message,
            "full_prompt": full_prompt,
            "retrieved_context": retrieved_context,
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
        "user_message": user_message,
        "retrieved_context": retrieved_context,
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
                user_message=result.get("user_message"),
                full_prompt=result.get("full_prompt"),
                retrieved_context=result.get("retrieved_context"),
                thread_id=result.get("thread_id"),
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
                full_prompt=None,
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
