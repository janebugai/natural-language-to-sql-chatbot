"""
Hybrid schema retrieval: ranks a domain's tables against a natural-language
question using a weighted blend of semantic similarity (embeddings) and
keyword/business-term matching, and returns every candidate's diagnostics
plus the top-k selection.

Deliberately domain-agnostic: nothing here knows a table or business term
by name. It only ever sees whatever TableMetadata/EmbeddedDocument objects
it's handed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag import config
from app.rag.embeddings import EmbeddedDocument, EmbeddingProvider, OpenAIEmbeddingProvider, cosine_similarity
from app.rag.models import TableMetadata

_WORD_RE = re.compile(r"[a-z0-9]+")

# Keyword-match weights: how much confidence one matched term contributes.
# A literal table-name mention is the strongest possible keyword signal; a
# business-term synonym or a raw column name are progressively weaker.
_TABLE_NAME_WEIGHT = 1.0
_TABLE_TERM_WEIGHT = 0.8
_COLUMN_WEIGHT = 0.6


@dataclass
class TableCandidate:
    """One table's retrieval diagnostics — every table gets one of these,
    not just the tables that made top_k, so a caller (or eval/) can see why
    something wasn't selected."""

    table_name: str
    qualified_name: str
    semantic_score: float
    keyword_score: float
    final_score: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    domain: str
    question: str
    top_k: int
    candidates: list[TableCandidate]  # every candidate, ranked descending by final_score

    @property
    def selected(self) -> list[TableCandidate]:
        """The top_k candidates actually chosen for context building."""
        return self.candidates[: self.top_k]

    @property
    def best_score(self) -> float:
        return self.candidates[0].final_score if self.candidates else 0.0


def retrieve(
    question: str,
    domain: str,
    embedded_documents: list[EmbeddedDocument],
    tables: dict[str, TableMetadata],
    provider: EmbeddingProvider | None = None,
    top_k: int | None = None,
    semantic_weight: float | None = None,
    keyword_weight: float | None = None,
) -> RetrievalResult:
    """
    Ranks every table with a document in `embedded_documents` against
    `question`. `tables` supplies the business terms/synonyms for keyword
    matching, kept separate from the embedded documents so this doesn't
    need to re-parse rendered document text to recover structured metadata.

    Only the question is embedded here — document embeddings are expected
    to already exist (see embeddings.embed_documents /
    load_cached_embeddings), so a request never pays for re-embedding the
    whole schema.
    """
    top_k = config.RAG_TOP_K if top_k is None else top_k
    semantic_weight = config.RAG_SEMANTIC_WEIGHT if semantic_weight is None else semantic_weight
    keyword_weight = config.RAG_KEYWORD_WEIGHT if keyword_weight is None else keyword_weight

    provider = provider or OpenAIEmbeddingProvider()
    question_vector = provider.embed([question])[0] if embedded_documents else []
    question_terms = _tokenize(question)
    question_lower = question.lower()

    candidates = []
    for doc in embedded_documents:
        semantic_score = cosine_similarity(question_vector, doc.vector)

        table = tables.get(doc.table_name)
        if table is not None:
            keyword_score, matched_terms = _keyword_score(question_terms, question_lower, table)
        else:
            keyword_score, matched_terms = 0.0, []

        final_score = semantic_weight * semantic_score + keyword_weight * keyword_score
        candidates.append(
            TableCandidate(
                table_name=doc.table_name,
                qualified_name=doc.qualified_name,
                semantic_score=round(semantic_score, 4),
                keyword_score=round(keyword_score, 4),
                final_score=round(final_score, 4),
                matched_terms=matched_terms,
            )
        )

    candidates.sort(key=lambda c: c.final_score, reverse=True)
    return RetrievalResult(domain=domain, question=question, top_k=top_k, candidates=candidates)


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _keyword_score(question_terms: set[str], question_lower: str, table: TableMetadata) -> tuple[float, list[str]]:
    """
    Combines weighted term matches with a noisy-OR: each matched term
    independently contributes some probability that this table is
    relevant, so score = 1 - product(1 - weight) over all matches. That
    keeps the result naturally bounded to [0, 1] without an arbitrary
    normalization constant, and multiple weak hits compound sensibly
    (two 0.6-weight hits score higher than one, but never reach 1.0 on
    their own the way a single table-name hit does).
    """
    matched: list[str] = []
    miss_probability = 1.0

    def check(term: str, weight: float) -> None:
        nonlocal miss_probability
        term_lower = term.strip().lower()
        if not term_lower:
            return
        # Multi-word terms (e.g. "return rate") match as a substring phrase;
        # single-word terms match as a whole token, so "product" doesn't
        # spuriously match inside "production".
        hit = term_lower in question_lower if " " in term_lower else term_lower in question_terms
        if hit:
            matched.append(term)
            miss_probability *= 1.0 - weight

    check(table.table_name.replace("_", " "), _TABLE_NAME_WEIGHT)
    for term in table.business_terms:
        check(term, _TABLE_TERM_WEIGHT)
    for col in table.columns:
        check(col.name.replace("_", " "), _COLUMN_WEIGHT)
        for term in col.business_terms:
            check(term, _COLUMN_WEIGHT)

    score = 1.0 - miss_probability
    seen: set[str] = set()
    unique_matched = [t for t in matched if not (t in seen or seen.add(t))]
    return score, unique_matched
