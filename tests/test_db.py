"""Safety validation and query execution (app/db.py)."""
from __future__ import annotations

import pytest

from app.db import UnsafeQueryError, get_schema_description, run_query, validate_sql


@pytest.mark.parametrize("bad_sql", [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET status = 'x'",
    "INSERT INTO orders VALUES (1)",
    "CREATE TABLE hack (x int)",
    "ALTER TABLE orders ADD COLUMN x int",
    "TRUNCATE orders",
    "ATTACH 'other.db' AS other",
    "COPY orders TO 'out.csv'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "SELECT 1; DROP TABLE orders",  # stacked statement
])
def test_unsafe_statements_are_rejected(bad_sql):
    with pytest.raises(UnsafeQueryError):
        validate_sql(bad_sql)


@pytest.mark.parametrize("good_sql", [
    "SELECT * FROM orders",
    "  select * from orders  ",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT * FROM orders;",  # trailing semicolon alone is fine, just stripped
])
def test_safe_selects_pass_validation(good_sql):
    validate_sql(good_sql)  # must not raise


def test_run_query_caps_rows():
    from app import db
    rows = run_query(f"SELECT * FROM order_items LIMIT {db.MAX_ROWS * 10}")
    assert len(rows) <= db.MAX_ROWS


def test_run_query_rejects_unsafe_sql():
    with pytest.raises(UnsafeQueryError):
        run_query("DROP TABLE orders")


def test_run_query_times_out_on_a_genuinely_slow_query(monkeypatch):
    from app import db
    monkeypatch.setattr(db, "QUERY_TIMEOUT_SECONDS", 0.3)
    with pytest.raises(TimeoutError):
        run_query("SELECT * FROM range(300000000) a, range(30) b ORDER BY a.range DESC")


def test_schema_description_hides_dbt_raw_seed_tables():
    schema = get_schema_description()
    assert "raw_" not in schema


def test_schema_description_lists_all_8_real_tables():
    schema = get_schema_description()
    assert schema.count("TABLE ") == 8
