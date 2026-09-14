"""
Database helpers: schema introspection and safe, read-only SQL execution.

Runs against DuckDB (demo.duckdb at the repo root), which is built by the
dbt project in dbt/ — see the README for how to (re)build it. Connections
are opened read-only, so nothing in this process can ever write to it, on
top of the SQL-level checks below.
"""
import re
import threading
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).parent.parent / "demo.duckdb"

# dbt's raw seed tables (raw_categories, raw_orders, ...) live in the same
# database as the marts the app actually queries. They're an implementation
# detail of the build, not part of the schema the app or the LLM should
# ever see.
_HIDDEN_TABLE_PATTERN = "raw_%"

# Statements that must never reach the database from model output. Beyond
# the usual write/DDL verbs, DuckDB has its own risk surface versus SQLite:
# COPY/EXPORT/IMPORT can read/write arbitrary files on disk, INSTALL/LOAD
# can pull in extensions, ATTACH/DETACH can open other database files.
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|GRANT|REVOKE|"
    r"ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|CALL|SET|PRAGMA)\b",
    re.IGNORECASE,
)

MAX_ROWS = 200
QUERY_TIMEOUT_SECONDS = 5


def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=True)


def get_schema_description() -> str:
    """
    Returns a plain-text description of every table and its columns,
    suitable for dropping straight into an LLM prompt. This is the
    "full schema" fallback path; when RAG_ENABLED is set, most requests
    use retrieved context instead of this.
    """
    conn = get_connection()
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "select table_name from duckdb_tables() "
                "where schema_name = 'main' and not internal "
                f"and table_name not like '{_HIDDEN_TABLE_PATTERN}' "
                "order by table_name"
            ).fetchall()
        ]

        lines = []
        for table in tables:
            cols = conn.execute(
                "select column_name, data_type from duckdb_columns() "
                "where schema_name = 'main' and table_name = ? order by column_index",
                [table],
            ).fetchall()
            col_descs = [f"{name} {dtype}" for name, dtype in cols]

            fks = conn.execute(
                "select constraint_column_names, referenced_table, referenced_column_names "
                "from duckdb_constraints() where schema_name = 'main' and table_name = ? "
                "and constraint_type = 'FOREIGN KEY'",
                [table],
            ).fetchall()
            fk_descs = [
                f"{fk_cols[0]} -> {ref_table}.{ref_cols[0]}"
                for fk_cols, ref_table, ref_cols in fks
            ]

            lines.append(f"TABLE {table} ({', '.join(col_descs)})")
            if fk_descs:
                lines.append(f"  foreign keys: {', '.join(fk_descs)}")
        return "\n".join(lines)
    finally:
        conn.close()


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
    returned regardless of what the model requested, and cancelling it if
    it runs longer than QUERY_TIMEOUT_SECONDS.
    """
    safe_sql = validate_sql(sql)
    conn = get_connection()
    timer = threading.Timer(QUERY_TIMEOUT_SECONDS, conn.interrupt)
    timer.start()
    try:
        cur = conn.execute(safe_sql)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS)
        return [dict(zip(columns, row)) for row in rows]
    except duckdb.InterruptException:
        raise TimeoutError(f"Query took longer than {QUERY_TIMEOUT_SECONDS}s and was cancelled.")
    finally:
        timer.cancel()
        conn.close()
