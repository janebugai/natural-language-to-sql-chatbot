"""
Computes retrieval + SQL metrics from eval/results.json (see evaluator.py).
Nothing here invents a number -- every metric is computed directly from
what was actually observed running the evaluation.

Run: python -m eval.metrics
"""
from __future__ import annotations

import json
from pathlib import Path

from app.llm import SQL_MODEL

QUESTIONS_PATH = Path(__file__).parent / "questions.json"
RESULTS_PATH = Path(__file__).parent / "results.json"


def load_questions() -> dict[str, dict]:
    return {q["id"]: q for q in json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))}


def load_results() -> list[dict]:
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))


def by_system(results: list[dict], system: str) -> list[dict]:
    return [r for r in results if r["system"] == system]


def in_scope(results: list[dict], questions: dict[str, dict]) -> list[dict]:
    """Questions answerable from this schema -- excludes out_of_scope (no such data in the data model)."""
    return [r for r in results if questions[r["question_id"]]["category"] != "out_of_scope"]


def out_of_scope(results: list[dict], questions: dict[str, dict]) -> list[dict]:
    return [r for r in results if questions[r["question_id"]]["category"] == "out_of_scope"]


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


# --------------------------------------------------------------------
# SQL-execution metrics (apply to either system)
# --------------------------------------------------------------------

def sql_execution_success_rate(results: list[dict]) -> float | None:
    return _rate(sum(r["success"] for r in results), len(results))


def repair_rate(results: list[dict]) -> float | None:
    return _rate(sum(r["repaired"] for r in results), len(results))


def repair_success_rate(results: list[dict]) -> float | None:
    repaired = [r for r in results if r["repaired"]]
    return _rate(sum(bool(r["repair_success"]) for r in repaired), len(repaired))


def invalid_reference_count(results: list[dict]) -> int:
    """Errors that look like a reference to a table/column that doesn't exist."""
    markers = ("does not exist", "not found", "binder error", "no such table", "no such column")
    return sum(1 for r in results if r["error"] and any(m in r["error"].lower() for m in markers))


def avg_schema_characters(results: list[dict]) -> float | None:
    if not results:
        return None
    return sum(r["schema_characters"] for r in results) / len(results)


def avg_generation_latency_ms(results: list[dict]) -> float | None:
    vals = [r["generation_latency_ms"] for r in results if r["generation_latency_ms"] is not None]
    return sum(vals) / len(vals) if vals else None


def avg_end_to_end_latency_ms(results: list[dict]) -> float | None:
    """generation_latency_ms alone for baseline; retrieval + generation for
    RAG (retrieval_latency_ms is 0/None for baseline rows, so this is safe
    to call on either)."""
    if not results:
        return None
    vals = [(r["generation_latency_ms"] or 0) + (r["retrieval_latency_ms"] or 0) for r in results]
    return sum(vals) / len(vals)


# --------------------------------------------------------------------
# Retrieval-quality metrics (RAG only)
# --------------------------------------------------------------------

def required_table_recall(rag_results: list[dict], questions: dict[str, dict]) -> float | None:
    """Mean, over questions with a non-empty expected_tables list, of
    |expected intersect final_context| / |expected|."""
    scores = []
    for r in rag_results:
        expected = set(questions[r["question_id"]]["expected_tables"])
        if not expected:
            continue
        scores.append(len(expected & set(r["tables_in_context"])) / len(expected))
    return sum(scores) / len(scores) if scores else None


def precision(rag_results: list[dict], questions: dict[str, dict]) -> float | None:
    """
    Excludes out-of-scope questions (empty expected_tables) -- there's no
    correct table for those, so scoring them as 0% precision would
    penalize the system for correctly declining rather than measure noise
    in genuinely answerable questions. required_table_recall() and
    top_k_retrieval_accuracy() already skip these the same way; precision()
    not doing so was an inconsistency, not a deliberate choice.
    """
    scores = []
    for r in rag_results:
        expected = set(questions[r["question_id"]]["expected_tables"])
        context = set(r["tables_in_context"])
        if not expected or not context:
            continue
        scores.append(len(expected & context) / len(context))
    return sum(scores) / len(scores) if scores else None


def top_k_retrieval_accuracy(rag_results: list[dict], questions: dict[str, dict]) -> float | None:
    """Fraction of in-scope questions where every expected table made it into the final context."""
    scored = []
    for r in rag_results:
        expected = set(questions[r["question_id"]]["expected_tables"])
        if not expected:
            continue
        scored.append(expected.issubset(set(r["tables_in_context"])))
    return _rate(sum(scored), len(scored))


def bridge_table_expansion_success(rag_results: list[dict], questions: dict[str, dict]) -> dict:
    """For questions tagged 'bridge_table': did graph expansion actually
    add whichever expected tables weren't already retrieved/value-matched?"""
    total = 0
    succeeded = 0
    details = []
    for r in rag_results:
        q = questions[r["question_id"]]
        if q["category"] != "bridge_table":
            continue
        total += 1
        expected = set(q["expected_tables"])
        seeded = set(r["retrieved_tables"]) | set(r["value_matched_tables"])
        needed_bridge = expected - seeded
        ok = needed_bridge.issubset(set(r["graph_added_tables"])) if needed_bridge else True
        succeeded += int(ok)
        details.append({
            "question_id": r["question_id"], "needed_bridge": sorted(needed_bridge),
            "graph_added": r["graph_added_tables"], "ok": ok,
        })
    return {"total": total, "succeeded": succeeded, "rate": _rate(succeeded, total), "details": details}


def out_of_scope_handling(results: list[dict], questions: dict[str, dict]) -> dict:
    """For out_of_scope questions: did the system decline (the sentinel
    'Cannot answer...' row, or a failed/empty execution) rather than
    fabricate a plausible-looking but bogus answer?"""
    rows = [r for r in results if questions[r["question_id"]]["category"] == "out_of_scope"]
    declined = 0
    for r in rows:
        is_declined = (
            not r["success"]
            or (r["sql"] is not None and "cannot answer" in r["sql"].lower())
            or r["row_count"] == 0
        )
        declined += int(is_declined)
    return {"total": len(rows), "declined": declined, "rate": _rate(declined, len(rows))}


# --------------------------------------------------------------------
# Report
# --------------------------------------------------------------------

def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _fmt_num(value: float | None, decimals: int = 0) -> str:
    return "n/a" if value is None else f"{value:.{decimals}f}"


def generate_report() -> str:
    questions = load_questions()
    results = load_results()
    baseline, rag = by_system(results, "baseline"), by_system(results, "rag")
    n_in_scope = sum(1 for q in questions.values() if q["category"] != "out_of_scope")
    n_out_of_scope = len(questions) - n_in_scope

    baseline_in, rag_in = in_scope(baseline, questions), in_scope(rag, questions)
    baseline_out, rag_out = out_of_scope(baseline, questions), out_of_scope(rag, questions)

    lines = [
        f"Questions: {len(questions)} total -- {n_in_scope} in-scope, {n_out_of_scope} out-of-scope (no such data in this schema)",
        f"Baseline model: {SQL_MODEL}",
        "",
        f"## In-scope questions (n={n_in_scope})",
        "| Metric | Baseline | RAG |",
        "|---|---:|---:|",
        f"| SQL execution success | {_fmt_pct(sql_execution_success_rate(baseline_in))} | {_fmt_pct(sql_execution_success_rate(rag_in))} |",
        f"| Repair rate | {_fmt_pct(repair_rate(baseline_in))} | {_fmt_pct(repair_rate(rag_in))} |",
        f"| Repair success rate | {_fmt_pct(repair_success_rate(baseline_in))} | {_fmt_pct(repair_success_rate(rag_in))} |",
        f"| Invalid table/column references | {invalid_reference_count(baseline_in)} | {invalid_reference_count(rag_in)} |",
        f"| Avg schema context size (characters) | {_fmt_num(avg_schema_characters(baseline_in))} | {_fmt_num(avg_schema_characters(rag_in))} |",
        f"| Avg end-to-end latency (ms) | {_fmt_num(avg_end_to_end_latency_ms(baseline_in))} | {_fmt_num(avg_end_to_end_latency_ms(rag_in))} |",
        f"| Required-table recall | -- | {_fmt_pct(required_table_recall(rag_in, questions))} |",
        f"| Precision | -- | {_fmt_pct(precision(rag_in, questions))} |",
        "",
        f"## Out-of-scope questions (n={n_out_of_scope})",
        "Correctly declined (returned the 'cannot answer' sentinel instead of fabricating a result):",
        "| Baseline | RAG |",
        "|---:|---:|",
        f"| {out_of_scope_handling(baseline_out, questions)['declined']}/{n_out_of_scope} | {out_of_scope_handling(rag_out, questions)['declined']}/{n_out_of_scope} |",
    ]

    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_report())
