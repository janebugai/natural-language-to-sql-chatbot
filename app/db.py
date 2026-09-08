"""
Database helpers: schema introspection and safe, read-only SQL execution.

This module is written against SQLite (matching demo.db) but the pattern
generalizes cleanly to Postgres/MySQL — swap the connection + a couple of
the introspection queries and everything else stays the same.
"""
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "demo.db"

# Statements that must never reach the database from model output.
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|ATTACH|REPLACE|GRANT|REVOKE|PRAGMA)\b",
    re.IGNORECASE,
)

MAX_ROWS = 200
QUERY_TIMEOUT_SECONDS = 5


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=QUERY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema_description() -> str:
    """
    Returns a plain-text description of every table and its columns,
    suitable for dropping straight into an LLM prompt.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row["name"] for row in cur.fetchall()]

    lines = []
    for table in tables:
        cur.execute(f"PRAGMA table_info({table})")
        cols = cur.fetchall()
        col_descs = [f"{c['name']} {c['type']}" for c in cols]

        cur.execute(f"PRAGMA foreign_key_list({table})")
        fks = cur.fetchall()
        fk_descs = [f"{fk['from']} -> {fk['table']}.{fk['to']}" for fk in fks]

        lines.append(f"TABLE {table} ({', '.join(col_descs)})")
        if fk_descs:
            lines.append(f"  foreign keys: {', '.join(fk_descs)}")

    conn.close()
    return "\n".join(lines)


class UnsafeQueryError(Exception):
    pass


def validate_sql(sql: str) -> str:
    """
    Rejects anything that isn't a plain SELECT, and strips a trailing
    semicolon + anything after it (defends against statement stacking).
    """
    sql = sql.strip().rstrip(";").strip()

    if ";" in sql:
        raise UnsafeQueryError("Multiple statements are not allowed.")

    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE):
        raise UnsafeQueryError("Only SELECT queries are allowed.")

    if FORBIDDEN_KEYWORDS.search(sql):
        raise UnsafeQueryError("Query contains a forbidden keyword.")

    return sql


def run_query(sql: str) -> list[dict]:
    """
    Validates and executes a read-only query, capping the number of rows
    returned regardless of what the model requested.
    """
    safe_sql = validate_sql(sql)
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(safe_sql)
        rows = cur.fetchmany(MAX_ROWS)
        return [dict(row) for row in rows]
    finally:
        conn.close()
