"""
Domain registry, business metadata loading, and physical+business merge
(app/rag/catalog/business_metadata.py).
"""
from __future__ import annotations

import logging

import pytest

from app.rag.catalog.business_metadata import (
    MetadataError,
    RegistryError,
    load_business_metadata,
    load_domain_registry,
    merge_metadata,
)
from app.rag.catalog.models import ColumnMetadata, TableMetadata


# --------------------------------------------------------------------
# registry loading
# --------------------------------------------------------------------

def test_registry_loads_ecommerce_domain(domain_registry):
    assert "ecommerce" in domain_registry.list_domains()
    entry = domain_registry.get("ecommerce")
    assert entry.backend == "duckdb"


def test_registry_unknown_domain_raises_with_helpful_message(domain_registry):
    with pytest.raises(RegistryError, match="Unknown domain 'finance'.*ecommerce"):
        domain_registry.get("finance")


def test_registry_missing_file_raises(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        load_domain_registry(tmp_path / "does_not_exist.yaml")


def test_registry_missing_metadata_file_key_raises(tmp_path):
    path = tmp_path / "schemas.yaml"
    path.write_text("schemas:\n  ecommerce:\n    backend: duckdb\n")
    with pytest.raises(RegistryError, match="metadata_file"):
        load_domain_registry(path)


def test_registry_empty_schemas_mapping_raises(tmp_path):
    path = tmp_path / "schemas.yaml"
    path.write_text("schemas: {}\n")
    with pytest.raises(RegistryError, match="non-empty"):
        load_domain_registry(path)


# --------------------------------------------------------------------
# business metadata loading
# --------------------------------------------------------------------

def test_load_real_ecommerce_metadata(domain_registry):
    business = load_business_metadata(domain_registry.metadata_path("ecommerce"))
    assert business["schema_name"] == "ecommerce"
    assert "orders" in business["tables"]


def test_missing_metadata_file_raises(tmp_path):
    with pytest.raises(MetadataError, match="not found"):
        load_business_metadata(tmp_path / "nope.yaml")


def test_malformed_yaml_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("schema_name: [unterminated\n  - broken: yaml: here")
    with pytest.raises(MetadataError, match="Invalid YAML"):
        load_business_metadata(path)


def test_non_mapping_top_level_raises(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- just\n- a\n- list\n")
    with pytest.raises(MetadataError, match="mapping"):
        load_business_metadata(path)


def test_missing_schema_name_key_raises(tmp_path):
    path = tmp_path / "no_name.yaml"
    path.write_text("description: whatever\n")
    with pytest.raises(MetadataError, match="schema_name"):
        load_business_metadata(path)


def test_tables_must_be_a_mapping(tmp_path):
    path = tmp_path / "bad_tables.yaml"
    path.write_text("schema_name: x\ntables:\n  - not\n  - a\n  - mapping\n")
    with pytest.raises(MetadataError, match="'tables'"):
        load_business_metadata(path)


# --------------------------------------------------------------------
# merge_metadata
# --------------------------------------------------------------------

def _table(name: str, columns: list[str]) -> TableMetadata:
    return TableMetadata(
        schema_name="main", table_name=name,
        columns=[ColumnMetadata(name=c, data_type="VARCHAR") for c in columns],
    )


def test_merge_applies_descriptions_and_terms():
    physical = [_table("widgets", ["widget_id", "color"])]
    business = {
        "schema_name": "test",
        "description": "A test domain",
        "domain_business_terms": ["gadgets"],
        "tables": {
            "widgets": {
                "description": "Things we sell",
                "business_terms": ["item"],
                "columns": {"color": {"description": "The widget's color", "business_terms": ["hue"]}},
            }
        },
    }
    domain = merge_metadata("test", "main", physical, business)
    assert domain.description == "A test domain"
    assert domain.business_terms == ["gadgets"]
    widgets = domain.tables["widgets"]
    assert widgets.description == "Things we sell"
    assert widgets.business_terms == ["item"]
    assert widgets.column("color").description == "The widget's color"
    assert widgets.column("color").business_terms == ["hue"]
    # untouched column keeps no description rather than erroring
    assert widgets.column("widget_id").description is None


def test_merge_warns_and_skips_stale_table_reference(caplog):
    physical = [_table("widgets", ["widget_id"])]
    business = {"schema_name": "t", "tables": {"gadgets": {"description": "no longer exists"}}}
    with caplog.at_level(logging.WARNING):
        domain = merge_metadata("t", "main", physical, business)
    assert "gadgets" in caplog.text
    assert "widgets" in domain.tables  # merge still succeeds for what does exist


def test_merge_warns_and_skips_stale_column_reference(caplog):
    physical = [_table("widgets", ["widget_id"])]
    business = {"schema_name": "t", "tables": {"widgets": {"columns": {"ghost_column": {"description": "x"}}}}}
    with caplog.at_level(logging.WARNING):
        domain = merge_metadata("t", "main", physical, business)
    assert "ghost_column" in caplog.text
    assert domain.tables["widgets"].description is None  # merge didn't crash, just skipped the bad column


def test_merge_folded_yaml_description_collapses_to_one_line():
    physical = [_table("widgets", ["widget_id"])]
    business = {
        "schema_name": "t",
        "tables": {"widgets": {"description": "Line one\nLine two\ncontinued\n"}},
    }
    domain = merge_metadata("t", "main", physical, business)
    assert domain.tables["widgets"].description == "Line one Line two continued"


def test_merge_metrics():
    business = {
        "schema_name": "t",
        "metrics": {"revenue": {"description": "Total sales", "synonyms": ["sales", "GMV"]}},
    }
    domain = merge_metadata("t", "main", [], business)
    assert domain.metrics["revenue"].description == "Total sales"
    assert domain.metrics["revenue"].synonyms == ["sales", "GMV"]


def test_real_ecommerce_domain_every_physical_column_has_a_description(ecommerce_domain):
    """Regression guard: every physical column in the real domain should be
    described in ecommerce.yaml -- a column with no description usually
    means the YAML fell behind a schema change."""
    undescribed = [
        f"{t.table_name}.{c.name}" for t in ecommerce_domain.tables.values() for c in t.columns if c.description is None
    ]
    assert undescribed == []
