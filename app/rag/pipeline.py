"""
Orchestrates catalog + retrieval + context_builder into the one entry
point app/main.py calls: given a question, either a compact RAG context
or a clear reason why it fell back to the full schema.

A domain's RagSession (merged metadata, FK graph, cached embeddings) is
built once per process and reused across requests -- see get_session().
Building a session never makes an OpenAI call, since it only ever reads
the already-committed embedding cache; only answer() can, to embed the
question itself.

Fallback to the full schema happens for two different reasons, both
reported via RagAnswerResult.fallback_reason so app/main.py and RAG_DEBUG
output can tell them apart:
  - "no_embedding_cache": the domain has no valid cache at all (e.g. a
    fresh clone that never ran `python -m app.rag.refresh`). Detected once
    at session load time; RAG stays disabled for that domain for the rest
    of the process rather than retrying on every request.
  - "retrieval_error": the cache is fine, but embedding the question
    itself failed (network issue, missing/invalid API key, etc.).
  - "low_confidence": retrieval succeeded but its best score didn't clear
    RAG_MIN_SCORE.

A retrieval-layer problem never raises out of answer() -- it's always
reported as a fallback, so a single request never fails outright just
because RAG had a bad day; the caller gets today's full-schema behavior
instead.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.rag import config
from app.rag.catalog.business_metadata import (
    MetadataError,
    RegistryError,
    load_business_metadata,
    load_domain_registry,
    merge_metadata,
)
from app.rag.catalog.models import DomainMetadata
from app.rag.catalog.physical_schema import build_schema_loader
from app.rag.context_builder import SchemaContext
from app.rag.context_builder import build as build_context
from app.rag.retrieval.embeddings import EmbeddedDocument, OpenAIEmbeddingProvider, load_cached_embeddings
from app.rag.retrieval.retriever import RetrievalResult, retrieve
from app.rag.retrieval.schema_documents import build_documents
from app.rag.retrieval.schema_graph import ExpansionResult, SchemaGraph, build_graph, expand

logger = logging.getLogger(__name__)


@dataclass
class RagSession:
    """Everything about one domain that's safe to build once and reuse
    across requests -- constructing this never makes an OpenAI call."""

    domain_name: str
    domain: DomainMetadata
    graph: SchemaGraph
    embedded_documents: list[EmbeddedDocument]
    enabled: bool
    disabled_reason: str | None = None


@dataclass
class RagAnswerResult:
    """What app/main.py needs to pick a schema text for the LLM, plus
    everything RAG_DEBUG surfaces."""

    used_rag: bool
    fallback_reason: str | None  # always None when used_rag is True
    domain: str
    context: SchemaContext | None = None
    retrieval_result: RetrievalResult | None = None
    expansion: ExpansionResult | None = None
    retrieval_latency_ms: float | None = None


_sessions: dict[str, RagSession] = {}


def get_session(domain_name: str) -> RagSession:
    """Builds (once per process) or returns the cached RagSession for a domain."""
    if domain_name not in _sessions:
        _sessions[domain_name] = _load_session(domain_name)
    return _sessions[domain_name]


def _load_session(domain_name: str) -> RagSession:
    try:
        registry = load_domain_registry()
        entry = registry.get(domain_name)
        loader = build_schema_loader(entry, registry.database_path(domain_name))
        physical_tables = loader.load_tables(entry.database_schema)
        business = load_business_metadata(registry.metadata_path(domain_name))
        domain = merge_metadata(domain_name, entry.database_schema, physical_tables, business)
    except (RegistryError, MetadataError, FileNotFoundError) as e:
        # A broken domain configuration is a startup-time problem worth
        # failing loudly on -- unlike a merely-missing embedding cache,
        # this means the domain can't even be described, RAG or no RAG.
        raise RuntimeError(f"Cannot load domain '{domain_name}': {e}") from e

    graph = build_graph(domain.tables)
    documents = build_documents(domain_name, domain.tables)
    embedded = load_cached_embeddings(domain_name, documents)

    if embedded is None:
        logger.warning(
            "No valid embedding cache for domain '%s'; RAG is disabled for it until "
            "`python -m app.rag.refresh --schema %s` is run. Falling back to the full schema.",
            domain_name, domain_name,
        )
        return RagSession(
            domain_name=domain_name, domain=domain, graph=graph, embedded_documents=[],
            enabled=False, disabled_reason="no_embedding_cache",
        )

    return RagSession(
        domain_name=domain_name, domain=domain, graph=graph, embedded_documents=embedded, enabled=True,
    )


def answer(question: str, domain_name: str) -> RagAnswerResult:
    """Returns either a usable RAG context or a clear fallback reason."""
    session = get_session(domain_name)

    if not session.enabled:
        return RagAnswerResult(used_rag=False, fallback_reason=session.disabled_reason, domain=domain_name)

    start = time.perf_counter()
    try:
        retrieval_result = retrieve(
            question, domain_name, session.embedded_documents, session.domain.tables,
            provider=OpenAIEmbeddingProvider(),
        )
    except Exception as e:
        logger.warning("Retrieval failed for domain '%s', falling back to full schema: %s", domain_name, e)
        return RagAnswerResult(used_rag=False, fallback_reason="retrieval_error", domain=domain_name)
    latency_ms = (time.perf_counter() - start) * 1000

    if retrieval_result.best_score < config.RAG_MIN_SCORE:
        logger.info(
            "Retrieval confidence too low for domain '%s' (best_score=%.3f < RAG_MIN_SCORE=%.3f); "
            "falling back to full schema.",
            domain_name, retrieval_result.best_score, config.RAG_MIN_SCORE,
        )
        return RagAnswerResult(
            used_rag=False, fallback_reason="low_confidence", domain=domain_name,
            retrieval_result=retrieval_result, retrieval_latency_ms=latency_ms,
        )

    expansion = expand(session.graph, [c.table_name for c in retrieval_result.selected])
    context = build_context(session.domain, expansion, question=question)

    return RagAnswerResult(
        used_rag=True, fallback_reason=None, domain=domain_name, context=context,
        retrieval_result=retrieval_result, expansion=expansion, retrieval_latency_ms=latency_ms,
    )
