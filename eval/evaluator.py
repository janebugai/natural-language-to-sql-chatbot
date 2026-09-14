"""
Runs eval/questions.json against both the baseline (full-schema) and RAG
pipelines, using the real database and real OpenAI calls (SQL generation,
and for RAG, question embeddings), and writes raw per-question results to
eval/results.json. metrics.py turns that into the numbers reported in
eval/README.md -- nothing here or there fabricates a result; every number
comes from an actual request/response recorded below.

Run: python -m eval.evaluator
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.db import UnsafeQueryError, get_schema_description, run_query
from app.llm import generate_sql, repair_sql
from app.rag import pipeline

QUESTIONS_PATH = Path(__file__).parent / "questions.json"
RESULTS_PATH = Path(__file__).parent / "results.json"

DOMAIN = "ecommerce"


@dataclass
class QuestionResult:
    question_id: str
    question: str
    system: str  # "baseline" | "rag"
    schema_characters: int
    tables_in_context: list[str]
    sql: str | None = None
    success: bool = False
    error: str | None = None
    repaired: bool = False
    repair_success: bool | None = None
    row_count: int | None = None
    generation_latency_ms: float | None = None
    # RAG-only fields (left at their defaults for system="baseline")
    used_rag: bool | None = None
    fallback_reason: str | None = None
    retrieved_tables: list[str] = field(default_factory=list)
    graph_added_tables: list[str] = field(default_factory=list)
    value_matched_tables: list[str] = field(default_factory=list)
    retrieval_latency_ms: float | None = None


def _generate_validate_and_maybe_repair(question: str, schema_text: str):
    """Shared execution path for both systems: generate -> validate/run ->
    one repair attempt on failure, mirroring app/main.py's real /ask flow
    exactly (so the eval measures the same behavior a real request gets)."""
    start = time.perf_counter()
    try:
        sql = generate_sql(question, schema_text)
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return None, False, f"generation failed: {e}", False, None, None, elapsed

    repaired = False
    repair_success = None
    rows = None
    error = None
    try:
        rows = run_query(sql)
        success = True
    except UnsafeQueryError as e:
        success, error = False, f"unsafe: {e}"
    except Exception as first_error:
        repaired = True
        try:
            sql = repair_sql(question, schema_text, sql, str(first_error))
            rows = run_query(sql)
            success, repair_success = True, True
        except Exception as e:
            success, error, repair_success = False, str(e), False

    latency_ms = (time.perf_counter() - start) * 1000
    row_count = len(rows) if rows is not None else None
    return sql, success, error, repaired, repair_success, row_count, latency_ms


def _run_baseline(q: dict) -> QuestionResult:
    schema_text = get_schema_description()
    sql, success, error, repaired, repair_success, row_count, latency = _generate_validate_and_maybe_repair(
        q["question"], schema_text
    )
    return QuestionResult(
        question_id=q["id"], question=q["question"], system="baseline",
        schema_characters=len(schema_text), tables_in_context=[],
        sql=sql, success=success, error=error, repaired=repaired, repair_success=repair_success,
        row_count=row_count, generation_latency_ms=latency,
    )


def _run_rag(q: dict) -> QuestionResult:
    rag_result = pipeline.answer(q["question"], DOMAIN)

    if rag_result.used_rag:
        schema_text = rag_result.context.text
        tables_in_context = rag_result.context.table_names
        graph_added = rag_result.expansion.bridge_tables if rag_result.expansion else []
        retrieved = [c.table_name for c in rag_result.retrieval_result.selected] if rag_result.retrieval_result else []
    else:
        schema_text = get_schema_description()
        tables_in_context, graph_added, retrieved = [], [], []

    sql, success, error, repaired, repair_success, row_count, latency = _generate_validate_and_maybe_repair(
        q["question"], schema_text
    )
    return QuestionResult(
        question_id=q["id"], question=q["question"], system="rag",
        schema_characters=len(schema_text), tables_in_context=tables_in_context,
        sql=sql, success=success, error=error, repaired=repaired, repair_success=repair_success,
        row_count=row_count, generation_latency_ms=latency,
        used_rag=rag_result.used_rag, fallback_reason=rag_result.fallback_reason,
        retrieved_tables=retrieved, graph_added_tables=graph_added,
        value_matched_tables=rag_result.value_matched_tables,
        retrieval_latency_ms=rag_result.retrieval_latency_ms,
    )


def run_evaluation() -> list[QuestionResult]:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    results: list[QuestionResult] = []
    for q in questions:
        print(f"[{q['id']}] {q['question']}")
        baseline_result = _run_baseline(q)
        rag_result = _run_rag(q)
        print(
            f"    baseline: success={baseline_result.success} "
            f"rag: success={rag_result.success} used_rag={rag_result.used_rag} "
            f"tables={rag_result.tables_in_context}"
        )
        results.append(baseline_result)
        results.append(rag_result)
    return results


def save_results(results: list[QuestionResult]) -> None:
    RESULTS_PATH.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} results to {RESULTS_PATH}")


if __name__ == "__main__":
    save_results(run_evaluation())
