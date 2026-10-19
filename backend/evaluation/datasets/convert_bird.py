"""Convert a BIRD-SQL dev split into the project's evaluation JSONL format.

Expected input layout (standard BIRD release):

    bird_dev/
        dev.json
        dev_databases/
            <db_id>/
                <db_id>.sqlite

Usage:
    cd backend
    .venv/bin/python -m evaluation.datasets.convert_bird \
        --input evaluation/datasets/bird_dev/dev.json \
        --db-dir evaluation/datasets/bird_dev/dev_databases \
        --output evaluation/datasets/bird_mini_dev.jsonl \
        --limit 100

The resulting JSONL cases carry the SQLite schema inline and a ``db_path``
field so the bird_eval runner can execute both the gold and generated SQL
locally against the original SQLite databases.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def _extract_schema(db_path: Path) -> str:
    """Return CREATE TABLE statements for all tables in a SQLite database."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    rows = cursor.fetchall()
    conn.close()
    return "\n".join(row[1] for row in rows if row[1])


def convert_bird_dev(
    input_path: Path,
    db_dir: Path,
    output_path: Path,
    limit: int | None = None,
) -> None:
    """Convert BIRD dev JSON to project JSONL cases."""
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if not db_dir.exists():
        raise FileNotFoundError(f"Database directory not found: {db_dir}")

    with input_path.open("r", encoding="utf-8") as f:
        raw_cases: list[dict[str, Any]] = json.load(f)

    if limit:
        raw_cases = raw_cases[:limit]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as out:
        for idx, case in enumerate(raw_cases, start=1):
            db_id = case["db_id"]
            sqlite_file = db_dir / db_id / f"{db_id}.sqlite"
            if not sqlite_file.exists():
                # Some BIRD distributions use .db extension.
                sqlite_file = db_dir / db_id / f"{db_id}.db"

            if not sqlite_file.exists():
                print(f"[warn] Skipping case {idx}: database not found {sqlite_file}")
                continue

            schema = _extract_schema(sqlite_file)
            if not schema.strip():
                print(f"[warn] Skipping case {idx}: empty schema {sqlite_file}")
                continue

            eval_case = {
                "index": idx,
                "question": case.get("question", ""),
                "schema": schema,
                "db_path": str(sqlite_file.resolve()),
                "expected_tool": "execute_sql",
                "expected_args": {"sql": case.get("SQL", "")},
                "expected_in_answer": [],
                "tags": ["bird", "nl2sql", db_id],
            }
            out.write(json.dumps(eval_case, ensure_ascii=False) + "\n")

    print(f"Written {len(raw_cases)} cases to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert BIRD-SQL dev to project JSONL")
    parser.add_argument("--input", required=True, type=Path, help="Path to dev.json")
    parser.add_argument("--db-dir", required=True, type=Path, help="Path to dev_databases directory")
    parser.add_argument("--output", required=True, type=Path, help="Output JSONL path")
    parser.add_argument("--limit", type=int, default=None, help="Limit to first N cases")
    args = parser.parse_args()

    convert_bird_dev(args.input, args.db_dir, args.output, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
