"""
FastAPI app: natural-language -> SQL -> safe execution -> results.

Run with:
    uvicorn app.main:app --reload
Then open http://localhost:8000/ for the chat UI, or POST to /ask with
{"question": "..."} to use the API directly.
"""
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_schema_description, run_query, UnsafeQueryError
from app.llm import generate_sql, repair_sql, summarize_results
from app.rag import config as rag_config
from app.rag import pipeline as rag_pipeline

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Domain selection is hardcoded for now -- there's only one registered
# domain (see app/metadata/schemas.yaml). See app/rag/catalog/business_metadata.py
# for the registry this would read from if/when a second domain exists.
RAG_DOMAIN = "ecommerce"

app = FastAPI(title="Natural Language to SQL Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pre-warm the RAG session at process startup (loads metadata + the
# embedding cache once, rather than on the first request) per the "don't
# make startup depend on unnecessary OpenAI calls" design -- this never
# calls OpenAI itself, it only reads what refresh.py already cached. Any
# failure here is logged, not fatal: the app must still come up and serve
# /ask via the full-schema fallback even if RAG can't be prepared.
if rag_config.RAG_ENABLED:
    try:
        rag_pipeline.get_session(RAG_DOMAIN)
    except Exception:
        logger.exception("Could not pre-warm RAG session for domain '%s' at startup.", RAG_DOMAIN)


class Question(BaseModel):
    question: str
    include_summary: bool = True


class AnswerResponse(BaseModel):
    question: str
    sql: str
    rows: list[dict]
    row_count: int
    summary: str | None = None
    rag_debug: dict | None = None  # only populated when RAG_DEBUG=true


@app.get("/", include_in_schema=False)
def chat_ui():
    """Serve the chat web UI. API docs remain at /docs."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/schema")
def schema():
    """Returns the current database schema (useful for debugging/UI display)."""
    return {"schema": get_schema_description()}


def _get_schema_context(question: str) -> tuple[str, rag_pipeline.RagAnswerResult | None]:
    """
    Picks the schema text to hand the LLM: retrieved+graph-expanded RAG
    context when it's available and confident, the full schema otherwise.
    Never raises -- any RAG-layer problem degrades to the full schema
    rather than failing the request, on top of the fallbacks pipeline.answer()
    already handles internally.
    """
    if not rag_config.RAG_ENABLED:
        return get_schema_description(), None

    try:
        rag_result = rag_pipeline.answer(question, RAG_DOMAIN)
    except Exception:
        logger.exception("RAG pipeline raised unexpectedly; falling back to the full schema.")
        return get_schema_description(), None

    if rag_result.used_rag:
        return rag_result.context.text, rag_result
    return get_schema_description(), rag_result


def _build_rag_debug(rag_result: rag_pipeline.RagAnswerResult | None) -> dict | None:
    if not rag_config.RAG_DEBUG or rag_result is None:
        return None

    debug = {
        "domain": rag_result.domain,
        "fallback_used": not rag_result.used_rag,
        "fallback_reason": rag_result.fallback_reason,
        "retrieval_latency_ms": rag_result.retrieval_latency_ms,
    }
    if rag_result.retrieval_result is not None:
        debug["retrieved_tables"] = [
            {
                "table": c.table_name,
                "semantic_score": c.semantic_score,
                "keyword_score": c.keyword_score,
                "final_score": c.final_score,
                "matched_terms": c.matched_terms,
            }
            for c in rag_result.retrieval_result.selected
        ]
    if rag_result.expansion is not None:
        debug["graph_added_tables"] = rag_result.expansion.bridge_tables
    if rag_result.context is not None:
        debug["final_tables"] = rag_result.context.table_names
        debug["context_characters"] = rag_result.context.character_count
    return debug


@app.post("/ask", response_model=AnswerResponse)
def ask(payload: Question):
    schema_text, rag_result = _get_schema_context(payload.question)

    try:
        sql = generate_sql(payload.question, schema_text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {e}")

    try:
        rows = run_query(sql)
    except UnsafeQueryError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Generated query was rejected for safety reasons: {e}. SQL was: {sql}",
        )
    except Exception as first_error:
        # One self-correction attempt: hand the model its query + the error.
        try:
            sql = repair_sql(payload.question, schema_text, sql, str(first_error))
            rows = run_query(sql)
        except UnsafeQueryError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Generated query was rejected for safety reasons: {e}. SQL was: {sql}",
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Query execution failed: {e}. SQL was: {sql}")

    summary = None
    if payload.include_summary:
        try:
            summary = summarize_results(payload.question, rows)
        except Exception:
            summary = None  # summary is a nice-to-have, don't fail the whole request

    return AnswerResponse(
        question=payload.question,
        sql=sql,
        rows=rows,
        row_count=len(rows),
        summary=summary,
        rag_debug=_build_rag_debug(rag_result),
    )


@app.get("/health")
def health():
    return {"status": "ok"}
