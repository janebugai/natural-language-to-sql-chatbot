"""Database schema extraction (app/rag/catalog/physical_schema.py)."""
from __future__ import annotations

import pytest

from app.rag.catalog.physical_schema import DuckDBSchemaLoader, SQLiteSchemaLoader, build_schema_loader
from app.rag.catalog.models import SchemaRegistryEntry


def test_discovers_all_real_tables(ecommerce_loader):
    tables = ecommerce_loader.load_tables("main")
    assert {t.table_name for t in tables} == {
        "categories", "customers", "products", "orders", "order_items", "reviews", "shipments", "returns",
    }


def test_hides_dbt_raw_seed_tables(ecommerce_loader):
    tables = ecommerce_loader.load_tables("main")
    assert not any(t.table_name.startswith("raw_") for t in tables)


def test_discovers_correct_foreign_keys(ecommerce_loader):
    tables = {t.table_name: t for t in ecommerce_loader.load_tables("main")}
    order_items = tables["order_items"]
    fk_targets = {(fk.column, fk.references_table, fk.references_column) for fk in order_items.foreign_keys}
    assert ("order_id", "orders", "order_id") in fk_targets
    assert ("product_id", "products", "product_id") in fk_targets


def test_primary_keys_detected(ecommerce_loader):
    tables = {t.table_name: t for t in ecommerce_loader.load_tables("main")}
    assert tables["orders"].primary_keys == ["order_id"]


def test_missing_database_file_raises(tmp_path):
    loader = DuckDBSchemaLoader(tmp_path / "does_not_exist.duckdb")
    with pytest.raises(FileNotFoundError):
        loader.load_tables("main")


def test_distinct_values_returns_none_over_cap(ecommerce_loader):
    # customers.email is effectively unique per row (~300 rows) -- well
    # over a small cap, so it must report "too high-cardinality" as None.
    assert ecommerce_loader.distinct_values("customers", "email", max_values=5) is None


def test_distinct_values_returns_values_under_cap(ecommerce_loader):
    values = ecommerce_loader.distinct_values("categories", "department", max_values=60)
    assert values is not None
    assert "Electronics" in values


def test_build_schema_loader_dispatches_on_backend(tmp_path):
    duckdb_entry = SchemaRegistryEntry(name="x", backend="duckdb", metadata_file="x.yaml")
    assert isinstance(build_schema_loader(duckdb_entry, tmp_path / "x.duckdb"), DuckDBSchemaLoader)

    sqlite_entry = SchemaRegistryEntry(name="x", backend="sqlite", metadata_file="x.yaml")
    assert isinstance(build_schema_loader(sqlite_entry, tmp_path / "x.db"), SQLiteSchemaLoader)

    bad_entry = SchemaRegistryEntry(name="x", backend="postgres", metadata_file="x.yaml")
    with pytest.raises(ValueError, match="Unknown backend"):
        build_schema_loader(bad_entry, tmp_path / "x")
