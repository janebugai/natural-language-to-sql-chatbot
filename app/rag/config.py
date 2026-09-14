"""
Centralized configuration for the RAG layer. Every tunable knob lives here,
read from environment variables with sensible defaults, so no other module
parses its own env vars or scatters magic numbers through the codebase.
"""
from __future__ import annotations

import os


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw is not None else default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw is not None else default


# Master switch: when False, the app falls back to sending the full schema
# to the LLM (the pre-RAG behavior) instead of retrieved context. Consumed
# by app/main.py once RAG is wired in, and by the baseline side of eval/.
RAG_ENABLED: bool = _bool_env("RAG_ENABLED", True)

# How many tables retrieval selects for the LLM's context, before any
# foreign-key graph expansion adds bridge tables on top.
RAG_TOP_K: int = _int_env("RAG_TOP_K", 3)

# Hybrid ranking weights (retriever.py). Both scores are already normalized
# to [0, 1], so keeping these two summing to 1.0 keeps final_score in a
# familiar [0, 1] range too, though nothing enforces that.
RAG_SEMANTIC_WEIGHT: float = _float_env("RAG_SEMANTIC_WEIGHT", 0.70)
RAG_KEYWORD_WEIGHT: float = _float_env("RAG_KEYWORD_WEIGHT", 0.30)

# Below this final_score, retrieval confidence is considered too low to
# trust -- app/rag/pipeline.py falls back to the full schema rather than
# proceed. Calibrated against real embeddings (text-embedding-3-small) on
# a handful of real questions, not guessed: a genuinely out-of-scope
# question scored ~0.05, while a *correct* retrieval with no keyword
# overlap at all (semantic signal only) scored ~0.17 -- comfortably-worded
# but keyword-free questions are common enough that 0.20 was cutting off
# good retrievals, not just bad ones. This is a first-pass calibration
# from a small sample; eval/ (once built) validates it properly across a
# real question set.
RAG_MIN_SCORE: float = _float_env("RAG_MIN_SCORE", 0.10)

# Hard cap on how many tables ever reach the LLM's context, after
# foreign-key graph expansion adds bridge tables on top of RAG_TOP_K. Stops
# a pathological join chain from ballooning the prompt back toward
# "the whole schema."
RAG_MAX_CONTEXT_TABLES: int = _int_env("RAG_MAX_CONTEXT_TABLES", 6)

# Surfaces retrieval internals (per-table scores, graph expansion, fallback
# reason) in the API response. Off by default -- this is debugging/eval
# information, not something a production client should depend on.
RAG_DEBUG: bool = _bool_env("RAG_DEBUG", False)

# Embedding model for both schema documents (embeddings.py) and the
# question itself (retriever.py) -- these must match, since cosine
# similarity between vectors from two different models is meaningless.
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
