"""Value-aware lookup (app/rag/retrieval/value_index.py)."""
from __future__ import annotations

from app.rag.retrieval.value_index import ValueIndex, build_value_index


def test_match_finds_a_known_value_case_insensitively():
    index = ValueIndex(entries={"kai patel": [("customers", "name")]})
    matches = index.match("what did Kai Patel buy")
    assert len(matches) == 1
    assert matches[0].table_name == "customers"
    assert matches[0].column_name == "name"


def test_match_finds_nothing_for_unrelated_question():
    index = ValueIndex(entries={"kai patel": [("customers", "name")]})
    assert index.match("what is the weather today") == []


def test_table_names_deduplicates_and_preserves_first_seen_order():
    index = ValueIndex(entries={
        "cancelled": [("orders", "status")],
        "acme": [("orders", "status")],  # same table via a different value
    })
    assert index.table_names("cancelled orders from acme") == ["orders"]


def test_longer_values_matched_over_shorter_ones_first():
    # "kai" alone isn't indexed here, only the full name -- checking longest
    # first just means we don't accidentally report a short false match
    # ahead of a legitimate longer one when both would match.
    index = ValueIndex(entries={"kai patel": [("customers", "name")], "patel": [("customers", "name")]})
    matches = index.match("kai patel")
    values_matched = [m.value for m in matches]
    assert values_matched[0] == "kai patel"  # longest checked/reported first


def test_build_value_index_against_real_database(ecommerce_loader, ecommerce_domain):
    index = build_value_index(ecommerce_loader, ecommerce_domain.tables)
    assert index.table_names("what's the most expensive product Kai Patel has bought") == ["customers"]
    assert index.table_names("how many orders were cancelled") == ["orders"]
    assert index.table_names("what is the weather like today") == []


def test_build_value_index_skips_high_cardinality_free_text(ecommerce_loader, ecommerce_domain):
    index = build_value_index(ecommerce_loader, ecommerce_domain.tables)
    columns_indexed = {col for locs in index.entries.values() for (_, col) in locs}
    assert "email" not in columns_indexed  # ~300 near-unique values, not useful for this kind of matching


def test_build_value_index_includes_name_pattern_and_categorical_columns(ecommerce_loader, ecommerce_domain):
    index = build_value_index(ecommerce_loader, ecommerce_domain.tables)
    locations = {loc for locs in index.entries.values() for loc in locs}
    assert ("customers", "name") in locations  # name-pattern column
    assert ("products", "product_name") in locations  # name-pattern column
    assert ("orders", "status") in locations  # low-cardinality categorical column
