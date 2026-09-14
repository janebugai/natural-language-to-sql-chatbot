"""
Embedding cache behavior (app/rag/retrieval/embeddings.py). Every test uses
a tmp_path cache dir and the FakeEmbeddingProvider -- never touches the
real committed cache or OpenAI.
"""
from __future__ import annotations

import json

from app.rag.retrieval.embeddings import EmbeddingCache, embed_documents, load_cached_embeddings
from app.rag.retrieval.schema_documents import SchemaDocument


def _doc(table: str, content: str) -> SchemaDocument:
    import hashlib
    return SchemaDocument(
        domain="d", table_name=table, qualified_name=f"main.{table}", content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )


def test_first_embed_calls_provider_for_every_document(fake_provider, tmp_path):
    docs = [_doc("a", "content a"), _doc("b", "content b")]
    cache = EmbeddingCache("d", cache_dir=tmp_path)
    embedded, stats = embed_documents("d", docs, provider=fake_provider, cache=cache)
    assert stats.reused == 0
    assert stats.regenerated == 2
    assert len(embedded) == 2
    assert cache.path.exists()


def test_second_embed_reuses_everything_with_unchanged_content(fake_provider, tmp_path):
    docs = [_doc("a", "content a"), _doc("b", "content b")]
    embed_documents("d", docs, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))

    fresh_cache = EmbeddingCache("d", cache_dir=tmp_path)
    embedded, stats = embed_documents("d", docs, provider=fake_provider, cache=fresh_cache)
    assert stats.reused == 2
    assert stats.regenerated == 0


def test_only_changed_document_gets_reembedded(fake_provider, tmp_path):
    docs = [_doc("a", "content a"), _doc("b", "content b")]
    embed_documents("d", docs, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))

    changed_docs = [_doc("a", "content a"), _doc("b", "CHANGED content b")]
    embedded, stats = embed_documents("d", changed_docs, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))
    assert stats.reused == 1
    assert stats.regenerated == 1


def test_stale_entry_pruned_when_document_removed(fake_provider, tmp_path):
    docs = [_doc("a", "content a"), _doc("b", "content b")]
    embed_documents("d", docs, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))

    only_a = [_doc("a", "content a")]
    embed_documents("d", only_a, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))

    raw = json.loads((tmp_path / "d.json").read_text())
    assert len(raw["entries"]) == 1  # b's stale entry was pruned, not left to accumulate forever


def test_cache_from_a_different_model_is_not_reused(fake_provider, tmp_path):
    docs = [_doc("a", "content a")]
    embed_documents("d", docs, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))

    class OtherModelProvider(type(fake_provider)):
        model = "a-different-model"

    _, stats = embed_documents("d", docs, provider=OtherModelProvider(), cache=EmbeddingCache("d", cache_dir=tmp_path))
    assert stats.reused == 0
    assert stats.regenerated == 1


def test_corrupted_cache_file_rebuilds_instead_of_crashing(fake_provider, tmp_path):
    (tmp_path / "d.json").write_text("{ not valid json !!")
    docs = [_doc("a", "content a")]
    embedded, stats = embed_documents("d", docs, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))
    assert stats.regenerated == 1
    assert len(embedded) == 1


def test_load_cached_embeddings_returns_none_when_missing(tmp_path):
    docs = [_doc("a", "content a")]
    assert load_cached_embeddings("d", docs, cache_dir=tmp_path) is None


def test_load_cached_embeddings_returns_none_when_stale(fake_provider, tmp_path):
    docs = [_doc("a", "content a"), _doc("b", "content b")]
    embed_documents("d", docs, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))

    changed_docs = [_doc("a", "content a"), _doc("b", "CHANGED")]
    assert load_cached_embeddings("d", changed_docs, cache_dir=tmp_path) is None  # b's hash isn't cached -> incomplete -> None


def test_load_cached_embeddings_returns_full_list_when_warm(fake_provider, tmp_path):
    docs = [_doc("a", "content a"), _doc("b", "content b")]
    embed_documents("d", docs, provider=fake_provider, cache=EmbeddingCache("d", cache_dir=tmp_path))

    loaded = load_cached_embeddings("d", docs, cache_dir=tmp_path)
    assert loaded is not None
    assert {e.table_name for e in loaded} == {"a", "b"}


def test_real_ecommerce_cache_is_warm_and_complete(ecommerce_domain):
    """The committed cache (app/rag/cache/ecommerce.json) should already
    cover every real table -- this is what lets the app boot with RAG
    enabled without an API key being required just to demo it."""
    from app.rag.retrieval.schema_documents import build_documents
    docs = build_documents("ecommerce", ecommerce_domain.tables)
    loaded = load_cached_embeddings("ecommerce", docs)
    assert loaded is not None
    assert len(loaded) == 8
