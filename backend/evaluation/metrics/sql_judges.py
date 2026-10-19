"""SQL execution-based judges for NL2SQL evaluations (e.g. BIRD-SQL).

These judges compare the result set of the gold SQL against the result set of
the generated SQL. Execution accuracy is the standard NL2SQL metric because
equivalent SQL can be written in many different ways.

Two backends are supported:
- SQLite (legacy): executes both SQLs against the SQLite db_path bundled with
  the BIRD dataset.
- StarRocks: executes both SQLs against the configured StarRocks database,
  which is used when the dataset has already been loaded into StarRocks and we
  want to evaluate the real agent path (DoSI + StarRocks).
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from decimal import Decimal
from typing import Any


def _normalize_value(value: Any) -> str:
    """Normalize a single cell value for comparison.

    - Numeric values are rounded to 5 decimal places so equivalent results do not
      fail because of floating-point noise (e.g. 459.95626 vs 459.956264287092).
    - Decimal objects and numeric strings are normalised the same way.
    - Strings that are clearly not numbers are lower-cased and stripped.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        rounded = round(float(value), 5)
        if rounded == int(rounded):
            return str(int(rounded))
        return str(rounded)

    text = str(value).strip().lower()
    # Treat purely numeric strings consistently with numbers (handles trailing zeros
    # like '1224.9600' vs '1224.96' and StarRocks Decimal strings).
    try:
        num = float(text)
    except ValueError:
        return text

    # Guard against treating non-numeric tokens such as date strings as numbers.
    # We only normalise when the string contains a decimal point, exponent, or
    # is a single '0' that would stay identical after rounding.
    if "." in text or "e" in text or text == "0":
        rounded = round(num, 5)
        if rounded == int(rounded):
            return str(int(rounded))
        return str(rounded)
    return text


def _normalize_rows(rows: list[tuple[Any, ...]]) -> list[tuple[str, ...]]:
    """Normalize and sort rows so result-set equality is order-independent."""
    normalized = [tuple(_normalize_value(cell) for cell in row) for row in rows]
    # Sort rows lexicographically; stable for comparison.
    return sorted(normalized)


# ------------------------------------------------------------------
# SQLite backend
# ------------------------------------------------------------------

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


# ------------------------------------------------------------------
# StarRocks backend
# ------------------------------------------------------------------

def _translate_sqlite_to_starrocks(sql: str, database: str) -> str:
    """Convert a SQLite-flavoured SQL to StarRocks SQL.

    The BIRD dataset ships gold SQL for SQLite, and the agent may also generate
    SQLite-style SQL because its prompt examples use SQLite syntax. When we run
    evaluation on the StarRocks copy of the same data we make minimal syntactic
    changes:
    - ``IIF(a, b, c)`` -> ``IF(a, b, c)``
    - ``STRFTIME('%Y', col)`` -> ``SUBSTR(col, 1, 4)``
    - ``STRFTIME('%m', col)`` -> ``SUBSTR(col, 6, 2)``
    - ``CAST(... AS REAL)`` -> ``CAST(... AS DOUBLE)``
    - bare ``BETWEEN 201301 AND 201312`` -> ``BETWEEN '201301' AND '201312'``
    - ``"table"`` identifiers -> `` `table` ``
    - unqualified table names -> ``database.table``
    """
    # SQLite IIF -> StarRocks IF.
    sql = re.sub(r"\bIIF\s*\(", "IF(", sql, flags=re.IGNORECASE)

    # SQLite STRFTIME -> StarRocks SUBSTR for text date columns.
    sql = re.sub(
        r"STRFTIME\s*\(\s*['\"]%Y['\"]\s*,\s*([^)]+)\s*\)",
        r"SUBSTR(\1, 1, 4)",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"STRFTIME\s*\(\s*['\"]%m['\"]\s*,\s*([^)]+)\s*\)",
        r"SUBSTR(\1, 6, 2)",
        sql,
        flags=re.IGNORECASE,
    )

    # SQLite REAL -> StarRocks DOUBLE.
    sql = re.sub(r"\bAS\s+REAL\b", "AS DOUBLE", sql, flags=re.IGNORECASE)

    # Quote bare YYYYMM integer literals in BETWEEN ... AND ... for text Date columns.
    sql = re.sub(
        r"\bBETWEEN\s+(\d{6})\s+AND\s+(\d{6})\b",
        r"BETWEEN '\1' AND '\2'",
        sql,
        flags=re.IGNORECASE,
    )

    # Remove SQLite-style double-quoted identifiers and backtick them.
    def _unquote_identifier(match: re.Match[str]) -> str:
        ident = match.group(1)
        return f"`{ident}`"

    sql = re.sub(r'"([^"]+)"', _unquote_identifier, sql)

    # Prefix bare table names with the database name.
    # A more robust approach would parse the SQL, but regex is enough for BIRD
    # gold SQL which is relatively simple.
    table_names = ["customers", "gasstations", "products", "yearmonth", "transactions_1k"]
    for table in table_names:
        # Match table name that is not already qualified (not preceded by `.` or backtick-word)
        pattern = rf"(?<!\w[.`])\b{re.escape(table)}\b"
        replacement = f"{database}.{table}"
        sql = re.sub(pattern, replacement, sql)

    return sql


def _execute_starrocks_sync(
    sql: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    timeout: float = 60.0,
) -> list[tuple[Any, ...]]:
    """Execute a SQL query against StarRocks synchronously using pymysql."""
    import pymysql

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        connect_timeout=int(timeout),
        read_timeout=int(timeout),
        write_timeout=int(timeout),
        cursorclass=pymysql.cursors.Cursor,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return list(cursor.fetchall())
    finally:
        conn.close()


async def execute_starrocks(
    sql: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    timeout: float = 60.0,
) -> list[tuple[Any, ...]]:
    """Execute a SQL query against StarRocks asynchronously."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _execute_starrocks_sync,
        sql,
        host,
        port,
        user,
        password,
        database,
        timeout,
    )


# ------------------------------------------------------------------
# Public judges
# ------------------------------------------------------------------

async def judge_sql_execution(
    gold_sql: str,
    generated_sql: str,
    db_path: str,
) -> tuple[bool, str]:
    """Compare gold and generated SQL by executing both on SQLite."""
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


async def judge_sql_execution_starrocks(
    gold_sql: str,
    generated_sql: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> tuple[bool, str]:
    """Compare gold and generated SQL by executing both on StarRocks.

    The gold SQL is translated from its SQLite flavour to StarRocks SQL.
    """
    if not generated_sql.strip():
        return False, "Generated SQL is empty"

    # Gold SQL ships in SQLite syntax; translate it so it can run on StarRocks.
    # Generated SQL is executed as-is: the agent must learn StarRocks dialect.
    gold_sql_sr = _translate_sqlite_to_starrocks(gold_sql, database)

    try:
        gold_rows = await execute_starrocks(
            gold_sql_sr, host, port, user, password, database
        )
    except Exception as exc:
        return False, f"Gold SQL execution failed: {exc}\nSQL: {gold_sql_sr}"

    try:
        pred_rows = await execute_starrocks(
            generated_sql, host, port, user, password, database
        )
    except Exception as exc:
        return False, f"Generated SQL execution failed: {exc}\nSQL: {generated_sql}"

    normalized_gold = _normalize_rows(gold_rows)
    normalized_pred = _normalize_rows(pred_rows)

    if normalized_gold == normalized_pred:
        return True, "Result sets match"

    reason = (
        f"Result sets differ: gold={json.dumps(normalized_gold, default=str)}, "
        f"pred={json.dumps(normalized_pred, default=str)}"
    )
    return False, reason


async def judge_sql_execution_hybrid(
    gold_sql: str,
    generated_sql: str,
    db_path: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> tuple[bool, str]:
    """Compare gold SQL (run on the original SQLite DB) with generated SQL (run on StarRocks).

    Use this when the BIRD dataset still ships its gold SQL and SQLite DB, but the
    agent is expected to produce StarRocks-native SQL against the StarRocks copy of
    the data. No dialect translation is applied to either query.
    """
    if not generated_sql.strip():
        return False, "Generated SQL is empty"

    try:
        gold_rows = await execute_sqlite(gold_sql, db_path)
    except Exception as exc:
        return False, f"Gold SQL execution failed on SQLite {db_path}: {exc}"

    try:
        pred_rows = await execute_starrocks(
            generated_sql, host, port, user, password, database
        )
    except Exception as exc:
        return False, f"Generated SQL execution failed on StarRocks: {exc}\nSQL: {generated_sql}"

    normalized_gold = _normalize_rows(gold_rows)
    normalized_pred = _normalize_rows(pred_rows)

    if normalized_gold == normalized_pred:
        return True, "Result sets match"

    reason = (
        f"Result sets differ: gold={json.dumps(normalized_gold, default=str)}, "
        f"pred={json.dumps(normalized_pred, default=str)}"
    )
    return False, reason


__all__ = [
    "execute_sqlite",
    "execute_starrocks",
    "judge_sql_execution",
    "judge_sql_execution_starrocks",
    "judge_sql_execution_hybrid",
    "_translate_sqlite_to_starrocks",
]
