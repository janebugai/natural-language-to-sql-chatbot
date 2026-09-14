"""
Integration test: question -> retrieval -> context -> SQL generation
interface, through the real FastAPI app and the real RAG pipeline against
the real ecommerce domain (real merged metadata, real FK graph, real value
index) -- with every OpenAI-backed call mocked, so this suite needs no
live API key or network access.

Document AND question embeddings both use FakeEmbeddingProvider (not the
real, committed OpenAI-vector cache) -- mixing a fake question vector with
real 1536-dim OpenAI document vectors would be a dimension mismatch, not
just an inaccurate score. The real ecommerce session (pipeline._sessions)
is swapped out for a fully self-consistent fake one for the duration of
this module and restored afterward.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rag import pipeline
from app.rag.retrieval.embeddings import EmbeddedDocument, embed_documents
from app.rag.retrieval.schema_documents import build_documents
from app.rag.retrieval.value_index import build_value_index
from tests.conftest import FakeEmbeddingProvider

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fake_ecommerce_session(ecommerce_domain, ecommerce_graph, ecommerce_loader):
    """Replaces the real (OpenAI-embedded) session with a self-consistent
    fake one built from the real domain/graph/value-index, for the
    duration of this test module only."""
    provider = FakeEmbeddingProvider()
    documents = build_documents("ecommerce", ecommerce_domain.tables)
    embedded, _ = embed_documents("ecommerce", documents, provider=provider, cache=_NullCache())
    value_index = build_value_index(ecommerce_loader, ecommerce_domain.tables)

    fake_session = pipeline.RagSession(
        domain_name="ecommerce", domain=ecommerce_domain, graph=ecommerce_graph,
        value_index=value_index, embedded_documents=embedded, enabled=True,
    )
    original = pipeline._sessions.get("ecommerce")
    pipeline._sessions["ecommerce"] = fake_session
    try:
        with patch("app.rag.pipeline.OpenAIEmbeddingProvider", FakeEmbeddingProvider):
            yield
    finally:
        if original is not None:
            pipeline._sessions["ecommerce"] = original
        else:
            pipeline._sessions.pop("ecommerce", None)


class _NullCache:
    """A throwaway, in-memory-only cache -- embed_documents() requires
    one, but this fixture never wants to touch the real on-disk cache."""

    def get(self, content_hash, model):
        return None

    def put(self, content_hash, vector, table_name):
        pass

    def prune_to(self, valid_hashes):
        pass

    def save(self, model):
        pass


@pytest.fixture(autouse=True)
def _mock_llm_calls():
    """Every test in this module mocks the OpenAI-backed chat calls -- no
    network access, no API key required to run this suite."""
    with patch("app.main.generate_sql") as mock_generate, \
         patch("app.main.repair_sql") as mock_repair, \
         patch("app.main.summarize_results") as mock_summarize:
        mock_summarize.return_value = "A mocked summary."
        yield mock_generate, mock_repair, mock_summarize


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_schema_endpoint_always_returns_full_schema():
    r = client.get("/schema")
    assert r.status_code == 200
    assert r.json()["schema"].count("TABLE ") == 8


def test_ask_happy_path(_mock_llm_calls):
    mock_generate, _, _ = _mock_llm_calls
    mock_generate.return_value = "SELECT customer_id, name FROM customers ORDER BY customer_id LIMIT 3"

    r = client.post("/ask", json={"question": "who are our first 3 customers?"})
    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 3
    assert body["summary"] == "A mocked summary."
    assert body["rag_debug"] is None  # RAG_DEBUG defaults to off


def test_ask_generate_sql_receives_rag_context_not_full_schema(_mock_llm_calls):
    """The whole point of steps 1-7: generate_sql should be handed a
    retrieved/graph-expanded slice, not always the entire 8-table schema."""
    mock_generate, _, _ = _mock_llm_calls
    mock_generate.return_value = "SELECT * FROM orders LIMIT 1"

    client.post("/ask", json={"question": "how many orders were cancelled"})
    schema_arg = mock_generate.call_args.args[1]
    assert schema_arg.count("TABLE ") < 8


def test_ask_uses_full_schema_when_rag_disabled(_mock_llm_calls, monkeypatch):
    from app.rag import config
    monkeypatch.setattr(config, "RAG_ENABLED", False)
    mock_generate, _, _ = _mock_llm_calls
    mock_generate.return_value = "SELECT * FROM orders LIMIT 1"

    client.post("/ask", json={"question": "anything at all"})
    schema_arg = mock_generate.call_args.args[1]
    assert schema_arg.count("TABLE ") == 8


def test_ask_repair_path_on_execution_failure(_mock_llm_calls):
    mock_generate, mock_repair, _ = _mock_llm_calls
    mock_generate.return_value = "SELECT nonexistent_column FROM orders"
    mock_repair.return_value = "SELECT order_id FROM orders LIMIT 1"

    r = client.post("/ask", json={"question": "anything"})
    assert r.status_code == 200
    assert mock_repair.called
    assert r.json()["sql"] == "SELECT order_id FROM orders LIMIT 1"


def test_ask_rejects_unsafe_generated_sql(_mock_llm_calls):
    mock_generate, _, _ = _mock_llm_calls
    mock_generate.return_value = "DROP TABLE orders"

    r = client.post("/ask", json={"question": "anything"})
    assert r.status_code == 400
    assert "rejected for safety reasons" in r.json()["detail"]


def test_ask_both_attempts_failing_returns_400_not_500(_mock_llm_calls):
    mock_generate, mock_repair, _ = _mock_llm_calls
    mock_generate.return_value = "SELECT nonexistent_column FROM orders"
    mock_repair.return_value = "SELECT another_bad_column FROM orders"

    r = client.post("/ask", json={"question": "anything"})
    assert r.status_code == 400
    assert "Query execution failed" in r.json()["detail"]


def test_ask_llm_failure_returns_502(_mock_llm_calls):
    mock_generate, _, _ = _mock_llm_calls
    mock_generate.side_effect = RuntimeError("simulated LLM outage")

    r = client.post("/ask", json={"question": "anything"})
    assert r.status_code == 502


def test_ask_include_summary_false_skips_summary_call(_mock_llm_calls):
    mock_generate, _, mock_summarize = _mock_llm_calls
    mock_generate.return_value = "SELECT * FROM orders LIMIT 1"

    r = client.post("/ask", json={"question": "anything", "include_summary": False})
    assert r.json()["summary"] is None
    assert not mock_summarize.called


def test_rag_debug_populated_when_enabled(_mock_llm_calls, monkeypatch):
    from app.rag import config
    monkeypatch.setattr(config, "RAG_DEBUG", True)
    mock_generate, _, _ = _mock_llm_calls
    mock_generate.return_value = "SELECT * FROM orders LIMIT 1"

    r = client.post("/ask", json={"question": "how many orders were cancelled"})
    debug = r.json()["rag_debug"]
    assert debug is not None
    assert debug["domain"] == "ecommerce"
    assert "final_tables" in debug
    assert debug["fallback_used"] is False
