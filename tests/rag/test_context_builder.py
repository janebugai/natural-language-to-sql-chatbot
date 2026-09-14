"""Compact schema context builder (app/rag/context_builder.py)."""
from __future__ import annotations

from app.rag import context_builder
from app.rag.catalog.models import ColumnMetadata, DomainMetadata, ForeignKey, MetricDefinition, TableMetadata
from app.rag.retrieval.schema_graph import ExpansionResult


def _domain() -> DomainMetadata:
    categories = TableMetadata(schema_name="main", table_name="categories", description="Categories.")
    products = TableMetadata(
        schema_name="main", table_name="products", description="Products.",
        columns=[
            ColumnMetadata(name="product_id", data_type="INTEGER", is_primary_key=True),
            ColumnMetadata(name="category_id", data_type="INTEGER", is_foreign_key=True),
        ],
        foreign_keys=[ForeignKey(column="category_id", references_table="categories", references_column="category_id")],
        business_terms=["item"],
    )
    return DomainMetadata(
        name="d", database_schema="main",
        tables={"categories": categories, "products": products},
        metrics={"revenue": MetricDefinition(name="revenue", description="Total sales.", synonyms=["sales"])},
    )


def _expansion(retrieved, bridge=()) -> ExpansionResult:
    return ExpansionResult(
        retrieved_tables=list(retrieved), bridge_tables=list(bridge),
        final_tables=list(retrieved) + list(bridge), paths_used=[], unreachable_pairs=[],
    )


def test_renders_description_and_columns_with_flags():
    ctx = context_builder.build(_domain(), _expansion(["products"]))
    assert "TABLE products" in ctx.text
    assert "Products." in ctx.text
    assert "product_id (INTEGER, primary key)" in ctx.text
    assert "category_id (INTEGER, foreign key)" in ctx.text


def test_bridge_table_gets_annotated():
    ctx = context_builder.build(_domain(), _expansion(["categories"], bridge=["products"]))
    assert "TABLE products  (included to complete a join path)" in ctx.text
    assert "TABLE categories\n" in ctx.text  # the retrieved one is NOT annotated


def test_relationships_only_for_edges_within_the_final_set():
    ctx = context_builder.build(_domain(), _expansion(["categories", "products"]))
    assert "products.category_id -> categories.category_id" in ctx.text


def test_no_relationships_section_when_only_one_table():
    ctx = context_builder.build(_domain(), _expansion(["categories"]))
    assert "Relationships:" not in ctx.text


def test_business_terms_section():
    ctx = context_builder.build(_domain(), _expansion(["products"]))
    assert "products: item" in ctx.text


def test_metrics_included_only_when_question_mentions_them():
    with_q = context_builder.build(_domain(), _expansion(["products"]), question="what is our total revenue")
    assert "revenue: Total sales." in with_q.text

    without_q = context_builder.build(_domain(), _expansion(["products"]))
    assert "Relevant business metrics" not in without_q.text

    unrelated_q = context_builder.build(_domain(), _expansion(["products"]), question="what color is the sky")
    assert "Relevant business metrics" not in unrelated_q.text


def test_metrics_matched_via_synonym():
    ctx = context_builder.build(_domain(), _expansion(["products"]), question="what were our sales last month")
    assert "revenue: Total sales." in ctx.text


def test_max_tables_never_drops_a_retrieved_table():
    ctx = context_builder.build(_domain(), _expansion(["categories", "products"]), max_tables=1)
    assert set(ctx.retrieved_table_names) == {"categories", "products"}
    assert ctx.table_names == ["categories", "products"]  # both retrieved tables kept despite the cap
    assert ctx.dropped_table_names == []


def test_max_tables_drops_bridge_tables_first():
    ctx = context_builder.build(_domain(), _expansion(["categories"], bridge=["products"]), max_tables=1)
    assert ctx.dropped_table_names == ["products"]
    assert ctx.table_names == ["categories"]


def test_custom_column_filter_changes_rendered_columns():
    def pk_only(table):
        return [c for c in table.columns if c.is_primary_key]

    ctx = context_builder.build(_domain(), _expansion(["products"]), column_filter=pk_only)
    assert "product_id" in ctx.text
    assert "category_id" not in ctx.text


def test_qualifies_names_only_when_multiple_schemas_present():
    domain = _domain()
    domain.tables["categories"].schema_name = "other_schema"
    ctx = context_builder.build(domain, _expansion(["categories", "products"]))
    assert "TABLE other_schema.categories" in ctx.text
    assert "TABLE main.products" in ctx.text


def test_character_count_matches_text_length():
    ctx = context_builder.build(_domain(), _expansion(["products"]))
    assert ctx.character_count == len(ctx.text)


def test_real_ecommerce_context_renders_without_error(ecommerce_domain, ecommerce_graph):
    from app.rag.retrieval.schema_graph import expand
    expansion = expand(ecommerce_graph, ["categories", "returns"])
    ctx = context_builder.build(ecommerce_domain, expansion, question="revenue from returned categories")
    assert set(ctx.bridge_table_names) == {"products", "order_items"}
    assert "revenue" in ctx.text.lower()
