"""Import the debit_card_specializing SQLite database into StarRocks.

Example::

    python -m evaluation.import_to_starrocks \
        --sqlite /path/to/debit_card_specializing.sqlite \
        --starrocks-host 127.0.0.1 --starrocks-query-port 9030 \
        --starrocks-http-port 8030 --user root --password '' \
        --database debit_card_specializing

The script:

1. Connects to StarRocks via the MySQL protocol port (default 9030).
2. Creates the target database and 5 tables.
3. Reads each SQLite table and stream-loads it through the HTTP API
   (default port 8030) in JSON-lines format.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pymysql
import requests

logger = logging.getLogger(__name__)

TABLE_SCHEMAS = {
    "customers": """
        CREATE TABLE IF NOT EXISTS {database}.customers (
            CustomerID BIGINT NOT NULL,
            Segment VARCHAR(16) NULL,
            Currency VARCHAR(8) NULL
        )
        DUPLICATE KEY(CustomerID)
        DISTRIBUTED BY HASH(CustomerID) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """,
    "gasstations": """
        CREATE TABLE IF NOT EXISTS {database}.gasstations (
            GasStationID BIGINT NOT NULL,
            ChainID BIGINT NULL,
            Country VARCHAR(8) NULL,
            Segment VARCHAR(32) NULL
        )
        DUPLICATE KEY(GasStationID)
        DISTRIBUTED BY HASH(GasStationID) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """,
    "products": """
        CREATE TABLE IF NOT EXISTS {database}.products (
            ProductID BIGINT NOT NULL,
            Description VARCHAR(512) NULL
        )
        DUPLICATE KEY(ProductID)
        DISTRIBUTED BY HASH(ProductID) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """,
    "yearmonth": """
        CREATE TABLE IF NOT EXISTS {database}.yearmonth (
            CustomerID BIGINT NOT NULL,
            Date VARCHAR(6) NOT NULL,
            Consumption DECIMAL(18, 4) NULL
        )
        DUPLICATE KEY(CustomerID, Date)
        DISTRIBUTED BY HASH(CustomerID) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """,
    "transactions_1k": """
        CREATE TABLE IF NOT EXISTS {database}.transactions_1k (
            TransactionID BIGINT NOT NULL,
            Date DATE NULL,
            Time VARCHAR(8) NULL,
            CustomerID BIGINT NULL,
            CardID BIGINT NULL,
            GasStationID BIGINT NULL,
            ProductID BIGINT NULL,
            Amount BIGINT NULL,
            Price DECIMAL(18, 4) NULL
        )
        DUPLICATE KEY(TransactionID)
        DISTRIBUTED BY HASH(TransactionID) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """,
}


def get_sqlite_rows(sqlite_path: Path, table: str) -> list[dict[str, Any]]:
    """Return all rows from a SQLite table as a list of dicts."""
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            cursor = conn.execute(f"SELECT * FROM \"{table}\"")
            rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
    return rows


def create_database(conn: pymysql.Connection, database: str) -> None:
    """Create the StarRocks database if it does not exist."""
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    logger.info("Database ensured: %s", database)


def create_tables(conn: pymysql.Connection, database: str) -> None:
    """Create all target tables in StarRocks."""
    for table, ddl in TABLE_SCHEMAS.items():
        ddl = ddl.format(database=database)
        with conn.cursor() as cur:
            cur.execute(ddl)
        logger.info("Table ensured: %s.%s", database, table)


def _ensure_stream_load_user(
    conn: pymysql.Connection, database: str, sl_user: str, sl_password: str
) -> None:
    """Create a dedicated stream-load user and grant privileges on the DB."""
    with conn.cursor() as cur:
        cur.execute(f"CREATE USER IF NOT EXISTS {sl_user} IDENTIFIED BY %s", (sl_password,))
        cur.execute(f"GRANT ALL ON {database}.* TO {sl_user}")
    conn.commit()


def stream_load(
    host: str,
    http_port: int,
    sl_user: str,
    sl_password: str,
    database: str,
    table: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Stream-load a list of dict rows into a StarRocks table.

    StarRocks FE may redirect to BE on a different port, which strips the
    ``Authorization`` header by default.  We therefore follow redirects
    manually and re-attach the credentials each time.
    """
    url = f"http://{host}:{http_port}/api/{database}/{table}/_stream_load"

    body = io.StringIO()
    for row in rows:
        body.write(json.dumps(row, ensure_ascii=False, default=str))
        body.write("\n")
    payload = body.getvalue().encode("utf-8")

    headers = {
        "Expect": "100-continue",
        "Content-type": "text/plain; charset=UTF-8",
        "format": "json",
        "strip_outer_array": "false",
        "ignore_default_value": "true",
        "autocommit": "true",
    }

    session = requests.Session()
    target = url
    for _ in range(5):
        response = session.put(
            target,
            data=payload,
            headers=headers,
            auth=(sl_user, sl_password),
            allow_redirects=False,
            timeout=120,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            target = response.headers.get("Location", target)
            continue
        break

    response.raise_for_status()
    result = response.json()
    if result.get("Status") not in ("OK", "Success"):
        raise RuntimeError(f"Stream load failed for {table}: {result}")
    return result


def import_database(args: argparse.Namespace) -> None:
    """Main entry point: create schema and import data."""
    sqlite_path = Path(args.sqlite)
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite file not found: {sqlite_path}")

    conn = pymysql.connect(
        host=args.starrocks_host,
        port=args.starrocks_query_port,
        user=args.user,
        password=args.password,
        connect_timeout=10,
    )
    try:
        create_database(conn, args.database)
        create_tables(conn, args.database)
        _ensure_stream_load_user(
            conn, args.database, args.stream_load_user, args.stream_load_password
        )
    finally:
        conn.close()

    for table in TABLE_SCHEMAS:
        logger.info("Reading SQLite table: %s", table)
        rows = get_sqlite_rows(sqlite_path, table)
        logger.info("Loaded %d rows from %s", len(rows), table)
        if not rows:
            logger.warning("Skipping empty table: %s", table)
            continue

        logger.info("Stream loading %s into StarRocks...", table)
        result = stream_load(
            host=args.starrocks_host,
            http_port=args.starrocks_http_port,
            sl_user=args.stream_load_user,
            sl_password=args.stream_load_password,
            database=args.database,
            table=table,
            rows=rows,
        )
        logger.info(
            "Loaded %s: NumberTotalRows=%s, NumberLoadedRows=%s, Message=%s",
            table,
            result.get("NumberTotalRows"),
            result.get("NumberLoadedRows"),
            result.get("Message"),
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Import debit_card_specializing SQLite data into StarRocks")
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=Path("/Users/yuguangde/Downloads/minidev/MINIDEV/dev_databases/debit_card_specializing/debit_card_specializing.sqlite"),
        help="Path to the SQLite database file.",
    )
    parser.add_argument("--starrocks-host", default="127.0.0.1")
    parser.add_argument("--starrocks-query-port", type=int, default=9030)
    parser.add_argument("--starrocks-http-port", type=int, default=8030)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="debit_card_specializing")
    parser.add_argument(
        "--stream-load-user",
        default="etl",
        help="Dedicated StarRocks user for stream load (will be created if missing).",
    )
    parser.add_argument(
        "--stream-load-password",
        default="etl",
        help="Password for the stream-load user.",
    )
    args = parser.parse_args()

    import_database(args)


if __name__ == "__main__":
    main()
