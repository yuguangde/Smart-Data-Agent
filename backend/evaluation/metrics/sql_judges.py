"""SQL execution-based judges for NL2SQL evaluations (e.g. BIRD-SQL).

These judges compare the result set of the gold SQL against the result set of
the generated SQL. Execution accuracy is the standard NL2SQL metric because
equivalent SQL can be written in many different ways.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any


def _normalize_value(value: Any) -> str:
    """Normalize a single cell value for comparison."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip().lower()


def _normalize_rows(rows: list[tuple[Any, ...]]) -> list[tuple[str, ...]]:
    """Normalize and sort rows so result-set equality is order-independent."""
    normalized = [tuple(_normalize_value(cell) for cell in row) for row in rows]
    # Sort rows lexicographically; stable for comparison.
    return sorted(normalized)


def _execute_sqlite_sync(sql: str, db_path: str, timeout: float = 30.0) -> list[tuple[Any, ...]]:
    """Execute a SQL query against a SQLite database synchronously."""
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        rows = cursor.fetchall()
        return [tuple(row) for row in rows]
    finally:
        cursor.close()
        conn.close()


async def execute_sqlite(sql: str, db_path: str, timeout: float = 30.0) -> list[tuple[Any, ...]]:
    """Execute a SQL query against a SQLite database asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _execute_sqlite_sync, sql, db_path, timeout
    )


async def judge_sql_execution(
    gold_sql: str,
    generated_sql: str,
    db_path: str,
) -> tuple[bool, str]:
    """Compare gold and generated SQL by executing both on the same database.

    Returns:
        ``(passed, reason)`` where ``passed`` is True when the normalized
        result sets are equal.
    """
    if not generated_sql.strip():
        return False, "Generated SQL is empty"

    try:
        gold_rows = await execute_sqlite(gold_sql, db_path)
    except Exception as exc:
        return False, f"Gold SQL execution failed: {exc}"

    try:
        pred_rows = await execute_sqlite(generated_sql, db_path)
    except Exception as exc:
        return False, f"Generated SQL execution failed: {exc}"

    normalized_gold = _normalize_rows(gold_rows)
    normalized_pred = _normalize_rows(pred_rows)

    if normalized_gold == normalized_pred:
        return True, "Result sets match"

    reason = (
        f"Result sets differ: gold={json.dumps(normalized_gold, default=str)}, "
        f"pred={json.dumps(normalized_pred, default=str)}"
    )
    return False, reason


__all__ = ["execute_sqlite", "judge_sql_execution"]
