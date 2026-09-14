"""
Shared fixtures for the whole test suite.

Nothing here requires a live OPENAI_API_KEY: FakeEmbeddingProvider stands
in for OpenAIEmbeddingProvider everywhere a test needs embeddings, using a
small deterministic bag-of-words vectorizer (real lexical overlap, not
random noise) rather than calling OpenAI. Tests that need the real
ecommerce database (demo.duckdb, built by dbt) skip with a clear message
if it hasn't been built yet, rather than failing confusingly.
"""
from __future__ import annotations

import hashlib

import pytest

from app.db import DB_PATH
from app.rag.catalog.business_metadata import load_business_metadata, load_domain_registry, merge_metadata
from app.rag.catalog.physical_schema import build_schema_loader
from app.rag.retrieval.embeddings import EmbeddingProvider
from app.rag.retrieval.schema_graph import build_graph

DOMAIN_NAME = "ecommerce"


@pytest.fixture(scope="session", autouse=True)
def _skip_db_tests_if_missing():
    if not DB_PATH.exists():
        pytest.skip(
            f"{DB_PATH} not found -- run `python scripts/generate_seed_data.py && "
            "dbt seed/run --project-dir dbt --profiles-dir dbt` first."
        )


class FakeEmbeddingProvider(EmbeddingProvider):
    """
    Deterministic, dependency-free stand-in for OpenAIEmbeddingProvider: a
    hashed bag-of-words vectorizer, so texts sharing vocabulary score
    genuinely higher on cosine similarity than unrelated ones (unlike pure
    random-hash noise), without any network call or API key.
    """

    model = "fake-bow-hash"
    DIM = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectorize(t) for t in texts]

    def _vectorize(self, text: str) -> list[float]:
        vec = [0.0] * self.DIM
        for word in text.lower().split():
            idx = int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.DIM
            vec[idx] += 1.0
        norm = sum(v * v for v in vec) ** 0.5
        return [v / norm for v in vec] if norm else vec


@pytest.fixture(scope="session")
def domain_registry():
    return load_domain_registry()


@pytest.fixture(scope="session")
def ecommerce_domain(domain_registry):
    """The real, merged (physical + business) ecommerce DomainMetadata."""
    entry = domain_registry.get(DOMAIN_NAME)
    loader = build_schema_loader(entry, domain_registry.database_path(DOMAIN_NAME))
    physical_tables = loader.load_tables(entry.database_schema)
    business = load_business_metadata(domain_registry.metadata_path(DOMAIN_NAME))
    return merge_metadata(DOMAIN_NAME, entry.database_schema, physical_tables, business)


@pytest.fixture(scope="session")
def ecommerce_loader(domain_registry):
    entry = domain_registry.get(DOMAIN_NAME)
    return build_schema_loader(entry, domain_registry.database_path(DOMAIN_NAME))


@pytest.fixture(scope="session")
def ecommerce_graph(ecommerce_domain):
    return build_graph(ecommerce_domain.tables)


@pytest.fixture
def fake_provider():
    return FakeEmbeddingProvider()
