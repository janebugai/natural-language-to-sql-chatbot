"""Deterministic schema documents + hashing (app/rag/retrieval/schema_documents.py)."""
from __future__ import annotations

from app.rag.catalog.models import ColumnMetadata, ForeignKey, TableMetadata
from app.rag.retrieval.schema_documents import build_document, build_documents


def _table() -> TableMetadata:
    return TableMetadata(
        schema_name="main", table_name="orders", description="Customer orders.",
        business_terms=["order", "purchase"],
        columns=[
            ColumnMetadata(name="order_id", data_type="INTEGER", is_primary_key=True, description="The id."),
            ColumnMetadata(name="customer_id", data_type="INTEGER", is_foreign_key=True,
                            business_terms=["buyer"]),
        ],
        foreign_keys=[ForeignKey(column="customer_id", references_table="customers", references_column="customer_id")],
    )


def test_document_is_deterministic():
    doc1 = build_document("ecommerce", _table())
    doc2 = build_document("ecommerce", _table())
    assert doc1.content == doc2.content
    assert doc1.content_hash == doc2.content_hash


def test_hash_changes_when_content_changes():
    table = _table()
    doc1 = build_document("ecommerce", table)
    table.description = "A different description."
    doc2 = build_document("ecommerce", table)
    assert doc1.content_hash != doc2.content_hash


def test_document_includes_expected_sections():
    doc = build_document("ecommerce", _table())
    assert "Table: main.orders" in doc.content
    assert "Customer orders." in doc.content
    assert "order_id - INTEGER - primary key - The id." in doc.content
    assert "customer_id - INTEGER - foreign key" in doc.content
    assert "main.orders.customer_id -> main.customers.customer_id" in doc.content
    assert "order, purchase, buyer" in doc.content


def test_table_with_no_description_or_relationships_or_terms_still_renders():
    bare = TableMetadata(schema_name="main", table_name="bare", columns=[ColumnMetadata(name="x", data_type="INTEGER")])
    doc = build_document("d", bare)
    assert "Table: main.bare" in doc.content
    assert "(no description available)" in doc.content
    assert "Relationships:" not in doc.content
    assert "Business terms:" not in doc.content


def test_build_documents_sorted_by_table_name():
    tables = {"zebra": TableMetadata(schema_name="main", table_name="zebra"),
              "apple": TableMetadata(schema_name="main", table_name="apple")}
    docs = build_documents("d", tables)
    assert [d.table_name for d in docs] == ["apple", "zebra"]


def test_real_ecommerce_documents_are_deterministic_across_two_builds(ecommerce_domain):
    docs1 = build_documents("ecommerce", ecommerce_domain.tables)
    docs2 = build_documents("ecommerce", ecommerce_domain.tables)
    assert [d.content_hash for d in docs1] == [d.content_hash for d in docs2]
    assert len(docs1) == 8
