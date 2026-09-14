"""Keyword scoring, semantic ranking, and hybrid retrieval (app/rag/retrieval/retriever.py)."""
from __future__ import annotations

from app.rag.catalog.models import ColumnMetadata, TableMetadata
from app.rag.retrieval.embeddings import EmbeddedDocument
from app.rag.retrieval.retriever import _keyword_score, _tokenize, retrieve


def _orders_table() -> TableMetadata:
    return TableMetadata(
        schema_name="main", table_name="orders", description="x",
        business_terms=["order", "purchase", "transaction", "sale"],
        columns=[ColumnMetadata(name="customer_id", data_type="INTEGER", business_terms=["buyer", "client"])],
    )


# --------------------------------------------------------------------
# keyword scoring
# --------------------------------------------------------------------

def test_table_name_match_scores_maximum():
    score, matched = _keyword_score(_tokenize("how many orders were placed"), "how many orders were placed", _orders_table())
    assert matched == ["orders"]
    assert score == 1.0


def test_business_term_match():
    score, matched = _keyword_score(_tokenize("who is our top buyer"), "who is our top buyer", _orders_table())
    assert matched == ["buyer"]
    assert abs(score - 0.6) < 1e-9


def test_multiple_hits_compound_via_noisy_or():
    score, matched = _keyword_score(_tokenize("every sale transaction"), "every sale transaction", _orders_table())
    assert set(matched) == {"sale", "transaction"}
    assert abs(score - (1 - (1 - 0.8) * (1 - 0.8))) < 1e-9


def test_no_match_scores_zero():
    score, matched = _keyword_score(_tokenize("what is the weather today"), "what is the weather today", _orders_table())
    assert score == 0.0 and matched == []


def test_single_word_terms_match_whole_word_only():
    products = TableMetadata(schema_name="main", table_name="products", description="x", business_terms=["product"])
    score, matched = _keyword_score(_tokenize("our production schedule"), "our production schedule", products)
    assert score == 0.0, "'product' must not match as a substring inside 'production'"


def test_multiword_terms_match_as_a_phrase():
    returns = TableMetadata(schema_name="main", table_name="returns", description="x", business_terms=["return rate"])
    score, matched = _keyword_score(
        _tokenize("what was our return rate last quarter"), "what was our return rate last quarter", returns
    )
    assert matched == ["return rate"] and score > 0


# --------------------------------------------------------------------
# retrieve() -- hybrid ranking, sorting, top_k, config wiring
# --------------------------------------------------------------------

def _embed(fake_provider, table_name: str, content: str) -> EmbeddedDocument:
    return EmbeddedDocument(
        domain="d", table_name=table_name, qualified_name=f"main.{table_name}", content=content,
        content_hash=table_name, vector=fake_provider.embed([content])[0], model=fake_provider.model,
    )


def test_every_candidate_gets_a_diagnostic_entry(fake_provider):
    tables = {"orders": _orders_table(), "categories": TableMetadata(schema_name="main", table_name="categories")}
    embedded = [_embed(fake_provider, "orders", "orders content"), _embed(fake_provider, "categories", "categories content")]
    result = retrieve("how many orders", "d", embedded, tables, provider=fake_provider, top_k=1)
    assert len(result.candidates) == 2  # every table, not just the selected top_k
    assert len(result.selected) == 1


def test_results_sorted_descending_by_final_score(fake_provider):
    tables = {"orders": _orders_table(), "categories": TableMetadata(schema_name="main", table_name="categories")}
    embedded = [_embed(fake_provider, "orders", "orders content"), _embed(fake_provider, "categories", "categories content")]
    result = retrieve("how many orders were placed", "d", embedded, tables, provider=fake_provider)
    scores = [c.final_score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
    assert result.candidates[0].table_name == "orders"


def test_keyword_only_weighting_ignores_semantic_score(fake_provider):
    tables = {"orders": _orders_table()}
    embedded = [_embed(fake_provider, "orders", "totally unrelated embedding content")]
    result = retrieve(
        "how many orders were placed", "d", embedded, tables, provider=fake_provider,
        semantic_weight=0.0, keyword_weight=1.0,
    )
    assert result.candidates[0].final_score == result.candidates[0].keyword_score


def test_top_k_defaults_from_config(fake_provider, monkeypatch):
    from app.rag import config
    monkeypatch.setattr(config, "RAG_TOP_K", 1)
    tables = {"orders": _orders_table(), "categories": TableMetadata(schema_name="main", table_name="categories")}
    embedded = [_embed(fake_provider, "orders", "x"), _embed(fake_provider, "categories", "y")]
    result = retrieve("orders", "d", embedded, tables, provider=fake_provider)
    assert result.top_k == 1
    assert len(result.selected) == 1


def test_best_score_is_top_candidates_score(fake_provider):
    tables = {"orders": _orders_table()}
    embedded = [_embed(fake_provider, "orders", "orders content")]
    result = retrieve("orders", "d", embedded, tables, provider=fake_provider)
    assert result.best_score == result.candidates[0].final_score


def test_empty_embedded_documents_returns_empty_result(fake_provider):
    result = retrieve("anything", "d", [], {}, provider=fake_provider)
    assert result.candidates == []
    assert result.best_score == 0.0
