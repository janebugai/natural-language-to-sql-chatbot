"""FK graph, shortest-path, and bridge-table expansion (app/rag/retrieval/schema_graph.py)."""
from __future__ import annotations

from app.rag.catalog.models import ForeignKey, TableMetadata
from app.rag.retrieval.schema_graph import SchemaGraph, build_graph, expand


def _tables_with_chain() -> dict[str, TableMetadata]:
    """categories -> products -> order_items -> returns, a simple chain."""
    categories = TableMetadata(schema_name="main", table_name="categories")
    products = TableMetadata(
        schema_name="main", table_name="products",
        foreign_keys=[ForeignKey(column="category_id", references_table="categories", references_column="category_id")],
    )
    order_items = TableMetadata(
        schema_name="main", table_name="order_items",
        foreign_keys=[ForeignKey(column="product_id", references_table="products", references_column="product_id")],
    )
    returns = TableMetadata(
        schema_name="main", table_name="returns",
        foreign_keys=[ForeignKey(column="order_item_id", references_table="order_items", references_column="order_item_id")],
    )
    return {t.table_name: t for t in [categories, products, order_items, returns]}


def test_build_graph_creates_undirected_adjacency():
    graph = build_graph(_tables_with_chain())
    assert graph.neighbors("categories") == {"products"}
    assert graph.neighbors("products") == {"categories", "order_items"}


def test_build_graph_skips_fk_to_table_not_in_domain():
    products = TableMetadata(
        schema_name="main", table_name="products",
        foreign_keys=[ForeignKey(column="category_id", references_table="categories", references_column="category_id")],
    )
    graph = build_graph({"products": products})  # categories isn't in the domain
    assert graph.neighbors("products") == set()
    assert graph.edges == []


def test_edge_between_returns_correct_direction_and_columns():
    graph = build_graph(_tables_with_chain())
    edge = graph.edge_between("products", "categories")
    assert edge.from_table == "products" and edge.from_column == "category_id"
    assert edge.to_table == "categories" and edge.to_column == "category_id"


def test_shortest_path_direct_edge():
    graph = build_graph(_tables_with_chain())
    assert graph.shortest_path("products", "categories") == ["products", "categories"]


def test_shortest_path_multi_hop():
    graph = build_graph(_tables_with_chain())
    assert graph.shortest_path("categories", "returns") == ["categories", "products", "order_items", "returns"]


def test_shortest_path_same_node():
    graph = build_graph(_tables_with_chain())
    assert graph.shortest_path("products", "products") == ["products"]


def test_shortest_path_unknown_node_returns_none():
    graph = build_graph(_tables_with_chain())
    assert graph.shortest_path("products", "nonexistent") is None


def test_shortest_path_disconnected_returns_none():
    graph = SchemaGraph()
    graph.adjacency = {"a": {"b"}, "b": {"a"}, "x": set()}
    assert graph.shortest_path("a", "x") is None


# --------------------------------------------------------------------
# expand()
# --------------------------------------------------------------------

def test_expand_spec_example_categories_and_returns():
    graph = build_graph(_tables_with_chain())
    result = expand(graph, ["categories", "returns"])
    assert set(result.bridge_tables) == {"products", "order_items"}
    assert set(result.final_tables) == {"categories", "returns", "products", "order_items"}
    assert result.was_expanded


def test_expand_already_adjacent_needs_no_bridge():
    graph = build_graph(_tables_with_chain())
    result = expand(graph, ["products", "order_items"])
    assert result.bridge_tables == []
    assert not result.was_expanded


def test_expand_single_table_is_a_no_op():
    graph = build_graph(_tables_with_chain())
    result = expand(graph, ["categories"])
    assert result.bridge_tables == []
    assert result.final_tables == ["categories"]


def test_expand_never_duplicates_a_table_already_retrieved():
    graph = build_graph(_tables_with_chain())
    result = expand(graph, ["categories", "products", "returns"])
    assert result.final_tables.count("products") == 1
    assert "products" not in result.bridge_tables  # it was retrieved, not bridged


def test_expand_disconnected_pair_reported_not_dropped():
    graph = SchemaGraph()
    graph.adjacency = {"a": {"b"}, "b": {"a"}, "x": {"y"}, "y": {"x"}}
    result = expand(graph, ["a", "x"])
    assert result.unreachable_pairs == [("a", "x")]
    assert result.bridge_tables == []
    assert set(result.final_tables) == {"a", "x"}


def test_expand_is_deterministic_across_repeated_calls():
    graph = build_graph(_tables_with_chain())
    paths = [expand(graph, ["categories", "returns"]).paths_used for _ in range(5)]
    assert all(p == paths[0] for p in paths)


def test_real_ecommerce_graph_has_expected_edge_count(ecommerce_graph):
    # 8 real foreign keys in the demo schema (see dbt/models/marts/_marts.yml)
    assert len(ecommerce_graph.edges) == 8
    assert ecommerce_graph.neighbors("order_items") == {"orders", "products", "returns"}
