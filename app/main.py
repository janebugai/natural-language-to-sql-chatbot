"""
FastAPI app: natural-language -> SQL -> safe execution -> results.

Run with:
    uvicorn app.main:app --reload
Then open http://localhost:8000/ for the chat UI, or POST to /ask with
{"question": "..."} to use the API directly.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.db import get_schema_description, run_query, UnsafeQueryError
from app.llm import generate_sql, summarize_results

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Natural Language to SQL Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Question(BaseModel):
    question: str
    include_summary: bool = True


class AnswerResponse(BaseModel):
    question: str
    sql: str
    rows: list[dict]
    row_count: int
    summary: str | None = None


@app.get("/", include_in_schema=False)
def chat_ui():
    """Serve the chat web UI. API docs remain at /docs."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/schema")
def schema():
    """Returns the current database schema (useful for debugging/UI display)."""
    return {"schema": get_schema_description()}


@app.post("/ask", response_model=AnswerResponse)
def ask(payload: Question):
    schema_text = get_schema_description()

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
    )


@app.get("/health")
def health():
    return {"status": "ok"}
