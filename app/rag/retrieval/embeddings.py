"""
Embedding generation, caching, and similarity — the only module that talks
to an embedding provider directly. Retrieval code depends on the
EmbeddedDocument/EmbeddingCache types here, never on OpenAI itself, so this
in-memory + NumPy implementation could be swapped for pgvector later
without changing retriever.py.

Embeddings are cached to a small JSON file per domain, keyed by each
document's content hash (see schema_documents.py). Refreshing a domain
re-embeds only documents whose hash changed since the last refresh — see
embed_documents() below.
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from openai import OpenAI

from app.rag import config
from app.rag.retrieval.schema_documents import SchemaDocument

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = config.EMBEDDING_MODEL
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"  # app/rag/cache/


class EmbeddingProvider(ABC):
    """Abstracts the embedding backend away from retrieval/caching logic."""

    model: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Returns one embedding vector per input text, in the same order."""


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL, client: OpenAI | None = None):
        self.model = model
        self._client = client  # created lazily so just importing this module never requires an API key

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
            self._client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]


@dataclass
class EmbeddedDocument:
    domain: str
    table_name: str
    qualified_name: str
    content: str
    content_hash: str
    vector: list[float]
    model: str


@dataclass
class RefreshStats:
    reused: int = 0
    regenerated: int = 0


class EmbeddingCache:
    """
    One JSON file per domain under app/rag/cache/, keyed by content hash.
    Plain text, not a binary format, so the cache can be opened and read
    directly instead of needing a tool to inspect it.
    """

    def __init__(self, domain: str, cache_dir: Path = CACHE_DIR):
        self.domain = domain
        self.path = cache_dir / f"{domain}.json"
        self._entries: dict[str, dict] = {}
        self._model: str | None = None
        self._load()

    @property
    def model(self) -> str | None:
        """The embedding model the cached vectors were built with, if any."""
        return self._model

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            self._model = raw.get("model")
            self._entries = raw.get("entries", {})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Embedding cache at %s is unreadable (%s); rebuilding from scratch.", self.path, e)
            self._entries = {}
            self._model = None

    def get(self, content_hash: str, model: str) -> list[float] | None:
        """Returns a cached vector only if it was built with the same model."""
        if self._model is not None and self._model != model:
            return None
        entry = self._entries.get(content_hash)
        return entry["vector"] if entry else None

    def put(self, content_hash: str, vector: list[float], table_name: str) -> None:
        self._entries[content_hash] = {"vector": vector, "table": table_name}

    def prune_to(self, valid_hashes: set[str]) -> None:
        """Drops cached entries for documents that no longer exist (renamed/dropped tables)."""
        self._entries = {h: e for h, e in self._entries.items() if h in valid_hashes}

    def save(self, model: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"domain": self.domain, "model": model, "entries": self._entries}
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        self._model = model


def embed_documents(
    domain: str,
    documents: list[SchemaDocument],
    provider: EmbeddingProvider | None = None,
    cache: EmbeddingCache | None = None,
) -> tuple[list[EmbeddedDocument], RefreshStats]:
    """
    Embeds a domain's schema documents, reusing cached vectors for any
    document whose content hash is unchanged and only calling the provider
    for the rest. Returns the embedded documents plus reuse/regeneration
    counts (used by refresh.py's summary output).
    """
    provider = provider or OpenAIEmbeddingProvider()
    cache = cache or EmbeddingCache(domain)
    model = provider.model

    stats = RefreshStats()
    to_embed: list[SchemaDocument] = []
    vectors_by_hash: dict[str, list[float]] = {}

    for doc in documents:
        cached = cache.get(doc.content_hash, model)
        if cached is not None:
            vectors_by_hash[doc.content_hash] = cached
            stats.reused += 1
        else:
            to_embed.append(doc)

    if to_embed:
        fresh_vectors = provider.embed([doc.content for doc in to_embed])
        for doc, vector in zip(to_embed, fresh_vectors):
            vectors_by_hash[doc.content_hash] = vector
            cache.put(doc.content_hash, vector, doc.table_name)
            stats.regenerated += 1

    cache.prune_to({doc.content_hash for doc in documents})
    cache.save(model)

    embedded = [
        EmbeddedDocument(
            domain=doc.domain,
            table_name=doc.table_name,
            qualified_name=doc.qualified_name,
            content=doc.content,
            content_hash=doc.content_hash,
            vector=vectors_by_hash[doc.content_hash],
            model=model,
        )
        for doc in documents
    ]
    return embedded, stats


def load_cached_embeddings(domain: str, documents: list[SchemaDocument]) -> list[EmbeddedDocument] | None:
    """
    Loads embeddings for a domain purely from cache — never calls the
    embedding provider. Used at application startup so the app doesn't make
    OpenAI calls just to boot. Returns None if the cache is missing or
    stale (any current document's hash isn't cached), so the caller can
    decide to fall back or trigger a refresh rather than silently querying
    against an incomplete index.
    """
    cache = EmbeddingCache(domain)
    if not cache.path.exists() or cache.model is None:
        return None

    embedded = []
    for doc in documents:
        vector = cache.get(doc.content_hash, cache.model)
        if vector is None:
            return None
        embedded.append(
            EmbeddedDocument(
                domain=doc.domain,
                table_name=doc.table_name,
                qualified_name=doc.qualified_name,
                content=doc.content,
                content_hash=doc.content_hash,
                vector=vector,
                model=cache.model,
            )
        )
    return embedded


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)
