"""
Fallback logic (app/rag/pipeline.py). Sessions are injected directly into
pipeline._sessions so these are true unit tests of answer()'s branching --
no real registry/database access, no OpenAI calls (FakeEmbeddingProvider
stands in), fully isolated from the real "ecommerce" session.
"""
from __future__ import annotations

import pytest

from app.rag import pipeline
from app.rag.catalog.models import ColumnMetadata, DomainMetadata, TableMetadata
from app.rag.retrieval.embeddings import EmbeddedDocument, EmbeddingProvider
from app.rag.retrieval.schema_graph import build_graph
from app.rag.retrieval.value_index import ValueIndex

DOMAIN = "test_domain"


@pytest.fixture(autouse=True)
def _clean_session_cache():
    """Every test gets a clean slate and cleans up after itself, so this
    suite never leaks a fake session into other tests (or the real app)."""
    pipeline._sessions.pop(DOMAIN, None)
    yield
    pipeline._sessions.pop(DOMAIN, None)


def _domain() -> DomainMetadata:
    orders = TableMetadata(
        schema_name="main", table_name="orders", description="Orders.",
        business_terms=["order"],
        columns=[ColumnMetadata(name="customer_id", data_type="INTEGER")],
    )
    return DomainMetadata(name=DOMAIN, database_schema="main", tables={"orders": orders})


def _embedded_doc(table_name: str, vector: list[float]) -> EmbeddedDocument:
    return EmbeddedDocument(
        domain=DOMAIN, table_name=table_name, qualified_name=f"main.{table_name}",
        content=f"Table: {table_name}", content_hash=table_name, vector=vector, model="fake",
    )


class _FixedVectorProvider(EmbeddingProvider):
    """Returns the same, controllable vector for any question -- lets a
    test dial in an exact semantic_score against a known document vector."""

    model = "fixed"

    def __init__(self, vector):
        self._vector = vector

    def embed(self, texts):
        return [self._vector for _ in texts]


class _RaisingProvider(EmbeddingProvider):
    model = "raising"

    def embed(self, texts):
        raise RuntimeError("simulated embedding API outage")


def _inject_session(*, enabled=True, disabled_reason=None, embedded_documents=(), value_index=None):
    domain = _domain()
    session = pipeline.RagSession(
        domain_name=DOMAIN, domain=domain, graph=build_graph(domain.tables),
        value_index=value_index or ValueIndex(), embedded_documents=list(embedded_documents),
        enabled=enabled, disabled_reason=disabled_reason,
    )
    pipeline._sessions[DOMAIN] = session
    return session


def test_disabled_session_falls_back_with_its_reason():
    _inject_session(enabled=False, disabled_reason="no_embedding_cache")
    result = pipeline.answer("anything", DOMAIN)
    assert result.used_rag is False
    assert result.fallback_reason == "no_embedding_cache"
    assert result.context is None


def test_retrieval_exception_falls_back_gracefully(monkeypatch):
    _inject_session(enabled=True, embedded_documents=[_embedded_doc("orders", [1.0, 0.0])])
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", _RaisingProvider)
    result = pipeline.answer("how many orders", DOMAIN)
    assert result.used_rag is False
    assert result.fallback_reason == "retrieval_error"


def test_low_confidence_falls_back(monkeypatch):
    # orthogonal vectors -> cosine similarity 0.0, and the question has no
    # keyword overlap either -> final_score 0.0, well under RAG_MIN_SCORE.
    _inject_session(enabled=True, embedded_documents=[_embedded_doc("orders", [0.0, 1.0])])
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", lambda: _FixedVectorProvider([1.0, 0.0]))
    result = pipeline.answer("completely unrelated gibberish", DOMAIN)
    assert result.used_rag is False
    assert result.fallback_reason == "low_confidence"


def test_value_match_overrides_low_confidence(monkeypatch):
    # Same orthogonal-vector setup as the low-confidence test above, but
    # this time a value_index match exists -- it must win.
    value_index = ValueIndex(entries={"kai patel": [("orders", "customer_name")]})
    _inject_session(enabled=True, embedded_documents=[_embedded_doc("orders", [0.0, 1.0])], value_index=value_index)
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", lambda: _FixedVectorProvider([1.0, 0.0]))
    result = pipeline.answer("what did kai patel buy", DOMAIN)
    assert result.used_rag is True
    assert result.value_matched_tables == ["orders"]
    assert "orders" in result.context.table_names


def test_successful_retrieval_builds_context(monkeypatch):
    _inject_session(enabled=True, embedded_documents=[_embedded_doc("orders", [1.0, 0.0])])
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", lambda: _FixedVectorProvider([1.0, 0.0]))
    result = pipeline.answer("how many orders were placed", DOMAIN)
    assert result.used_rag is True
    assert result.fallback_reason is None
    assert "orders" in result.context.table_names
    assert result.retrieval_latency_ms is not None


def test_get_session_caches_across_calls():
    session = _inject_session(enabled=True)
    assert pipeline.get_session(DOMAIN) is session  # returns the cached instance, doesn't rebuild
