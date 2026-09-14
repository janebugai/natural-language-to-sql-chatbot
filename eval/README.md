# Evaluation

Baseline (full schema) vs RAG, on 30 real questions against the real
database, through the app's real generate → validate → execute → repair
flow. Graded on expected tables + execution success, not exact SQL
(multiple correct queries can answer the same question).

```bash
python -m eval.evaluator   # real OpenAI calls (~$0.01-0.05); writes eval/results.json
python -m eval.metrics      # prints the report below
```

`eval/results.json` is committed so these numbers don't require re-running.
Config at eval time: `RAG_TOP_K=4`, `RAG_MIN_SCORE=0.10`, `RAG_MAX_CONTEXT_TABLES=6`.

## In-scope questions (n=27)

Answerable from this schema.

| Metric | Baseline | RAG |
|---|---:|---:|
| **SQL execution success** | **96%** | **100%** |
| Repair rate | 11% | 7% |
| Repair success rate | 67% | 100% |
| Invalid table/column references | 1 | 0 |
| Avg schema context size (chars) | 1599 | 1763 |
| Avg end-to-end latency (ms) | 948 | 1733 |
| Required-table recall | — | 100% |
| Precision | — | 40% |

## Out-of-scope questions (n=3)

No such data in this schema (e.g. "which employees processed the most
returns" — no `employees` table). Correctly declined (`SELECT 'Cannot
answer with available schema' AS error`) instead of fabricating a result:

| Baseline | RAG |
|---:|---:|
| 3/3 | 3/3 |

## Notable findings

- **RAG's one execution win (q21)** is a real, inspectable case: baseline's
  full-schema context led to a query with a genuine `GROUP BY` bug that
  repair didn't fix; RAG's context (which includes the `revenue` metric's
  explicit guidance) led to a simpler, correct query first try.
- **Recall 100%, precision 40%**: `RAG_TOP_K=4` sends 4 tables regardless
  of question complexity, so single-table questions land near 25%
  precision (1 needed of 4 sent). `top_k` was raised from 3 to fix a real
  recall miss (see `app/rag/config.py`) — a recall/precision trade, not free.
- **RAG's context is larger than the full schema (1763 vs 1599 chars)**:
  the full-schema format is a bare column list; RAG's includes
  descriptions/business terms per spec. Expected to flip on a larger
  schema; not demonstrated here, since this schema is only 8 tables.
- **A false success, found by inspection, not by any metric above**: q12
  ("products never returned") — baseline and RAG generated the *identical*
  query, executed cleanly, returned a full page of results, and was
  wrong: a one-to-many JOIN fan-out (`WHERE returns.return_id IS NULL` per
  order-item row, not `NOT EXISTS` per product). Uncapped, it returns 6,584
  rows covering all 84 of 84 products; the real answer is 5. Both systems
  had the right tables — this is a SQL-generation bug, not a retrieval one.

## Limitations

- 30 questions / one domain / one run — directional, not statistically precise.
- Grading is table-level, not answer-level — q12 above is real evidence this misses bugs.
- No repeated-trial variance analysis.
