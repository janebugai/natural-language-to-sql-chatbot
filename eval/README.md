# Evaluation

Compares the baseline (always send the full 8-table schema) against the
RAG pipeline (retrieval + graph expansion + value-aware matching) on the
same 30 questions, against the same real database, through the same
generate → validate → execute → repair flow `app/main.py` actually runs.

## Running it

```bash
python -m eval.evaluator   # makes real OpenAI calls (~$0.01-0.05 total); writes eval/results.json
python -m eval.metrics      # reads eval/results.json, prints the report below
```

`eval/results.json` is committed so the numbers below are reproducible
without re-spending API calls; re-run `evaluator.py` yourself to regenerate
it (LLM output at `temperature=0` and OpenAI embeddings are both
deterministic for the same input, so a re-run should closely reproduce
these numbers, modulo any drift in the underlying models over time).

## Setup

- **Domain**: `ecommerce` (the only registered domain — 8 tables, real dbt-built data)
- **Questions**: `questions.json`, 30 questions covering single-table
  queries, aggregations, joins, multi-joins, date filters, rankings,
  business-term synonyms, bridge-table requirements, an ambiguous question,
  and out-of-scope questions — see the file for each question's
  `expected_tables`/`category`.
- **Baseline model**: `gpt-4o-mini` (`SQL_MODEL`, unchanged from the
  pre-RAG app)
- **RAG configuration at eval time**: `RAG_TOP_K=4`, `RAG_SEMANTIC_WEIGHT=0.7`,
  `RAG_KEYWORD_WEIGHT=0.3`, `RAG_MIN_SCORE=0.10`, `RAG_MAX_CONTEXT_TABLES=6`
  (see `app/rag/config.py` for how these defaults were arrived at)
- Grading is against **expected tables + execution success**, not exact
  "gold SQL" — deliberately, since your spec explicitly discourages exact
  SQL-string equality (multiple correct queries can answer the same
  question) and hand-authoring 30 verified-correct gold queries wasn't a
  good effort/value trade-off versus a defensible, spec-aligned
  alternative.

## Results (actual run, 2026-09-14)

| Metric | Baseline | RAG |
|---|---:|---:|
| SQL execution success | 97% | 100% |
| Repair rate | 10% | 7% |
| Repair success rate | 67% | 100% |
| Invalid table/column references | 1 | 0 |
| Avg schema context size (characters) | 1599 | 1748 |
| Avg SQL-generation latency (ms, incl. any repair) | 913 | 936 |
| Avg end-to-end latency (ms; RAG includes retrieval) | 913 | 1685 |

RAG-specific retrieval metrics:
- **Required-table recall: 100%** — every question's expected tables ended up in the final context
- **Precision: 40%** (on the 27 in-scope questions; out-of-scope questions are excluded, since there's no "correct" table to score against — see Limitations)
- **Top-k retrieval accuracy: 100%** (same measurement as recall, stricter framing: all-or-nothing per question)
- **Bridge-table expansion: not exercised by this run** — see Limitations
- **Avg retrieval-only latency: 749ms**

Out-of-scope handling (declined rather than fabricated an answer):
- Baseline: 3/3 — Rag: 3/3 — **both systems handled all 3 out-of-scope questions (q28–30) correctly**, returning the `SELECT 'Cannot answer with available schema' AS error` sentinel rather than inventing a plausible-looking wrong answer.

## What these numbers actually show

**A concrete correctness win for RAG, not just a retrieval exercise.**
q21 ("average order value by acquisition channel") is the one baseline
failure: given the full schema, `gpt-4o-mini` wrote a query aggregating
`shipping_cost`/`tax` in a subquery that hit a real `GROUP BY` bug, and its
one repair attempt didn't fix it. RAG's context — which includes the
`revenue` metric's explicit guidance ("sum `order_items.net_amount`... do
not re-sum `customers.lifetime_revenue`") — led the model to a simpler,
correct query on the first attempt. This is a small sample (one question),
but it's a real, inspectable example of *why* narrower, better-annotated
context can outperform "just show it everything," not merely a retrieval
metric.

**Recall is high, precision is not — and that's a real, honest limitation.**
100% of needed tables always made it into context, but on average only 40%
of the tables *in* context were actually needed (on the 27 in-scope
questions). Looking at the per-question breakdown (`eval/results.json`),
the dominant cause is simple: `RAG_TOP_K=4` sends 4 tables regardless of
how many the question actually needs, so every single-table question
(q01, q02, q04, q06, q07, ...) lands at exactly 25% precision — 1 needed
table out of 4 sent. `RAG_TOP_K=4` was set generously (see
`app/rag/config.py`) specifically to fix a real retrieval miss found
during manual testing (the "Kai Patel" case) — that margin helps recall
but directly costs precision on the simpler questions that didn't need it.
2. Value-aware matching and semantic/keyword retrieval are unioned, not
   ranked against each other — a table that value-matches is always
   included even if the question barely needs it.

**Schema context is *larger* than the full schema on average — a finding
from the actual data, not a single cherry-picked case.** 1748 vs 1599
characters. As found and documented while wiring RAG into the app (see
`app/rag/context_builder.py` and the git history), this demo's full-schema
format is a bare `TABLE x (col type, col type)` list with zero business
context, while the RAG context includes descriptions, business terms, and
relevant metrics per your spec's item 16. On this 8-table schema, "fewer
tables" doesn't outweigh "richer per-table text." **This is expected to
flip on a real, larger schema** (dozens/hundreds of tables), where the
full-schema baseline itself would already be huge — but that's a
prediction, not something this eval demonstrates, and it's called out here
rather than left implicit.

**Bridge-table expansion wasn't actually exercised by this question set at
`RAG_TOP_K=4`.** Both questions tagged `bridge_table` (q14, q15) had all
their expected tables already covered by direct retrieval or value-matching
before graph expansion ever ran — raising `top_k` to fix the Kai Patel
recall miss incidentally made this eval's two bridge-table questions too
easy to need a bridge. This does **not** mean bridge expansion doesn't
work: it's covered thoroughly by real, non-eval evidence — the exact
`categories + returns → products + order_items` example from your spec is
verified in `tests/rag/retrieval/test_schema_graph.py` and was manually
confirmed end-to-end through the live app earlier in development. It does
mean this eval's own bridge-table category isn't currently pulling its
weight as a check on that behavior specifically. A future eval revision
should add a question engineered to need a bridge at the current `top_k`
(e.g. by picking two tables with weak direct scores against each other but
connected only through an intermediate table), rather than relying on q14/q15.

**End-to-end latency is real and non-trivial.** RAG adds ~750ms per
request (one embedding call before generation ever starts) — 1685ms vs
913ms end-to-end on average. For a chat-style UI this is noticeable, if
not prohibitive.

**The context cap works as designed, not just in unit tests.** q22 ("how
many returns were due to being defective") pulled in enough graph-expanded
tables to hit `RAG_MAX_CONTEXT_TABLES=6`; `context_builder.py` correctly
dropped a bridge table (`products`) rather than a retrieved one, and the
query still succeeded (it only needed `returns`) — real evidence the cap
degrades gracefully rather than breaking things.

## Limitations of this evaluation itself

- **30 questions, one domain, one run.** Large enough to catch real
  patterns (as it did — the q21 example, the precision finding) but too
  small for the percentages above to be statistically precise. A repair
  rate of "7%" is 2 out of 30 questions; treat these as directional, not
  exact.
- **Grading is table-level, not column-level or answer-level.** A query
  can touch all the right tables and still compute the wrong thing; this
  eval wouldn't catch that (see Recommended next steps).
- **No adversarial or ambiguous-question grading beyond execution
  success.** q27 (ambiguous date range) executed successfully for both
  systems, but "ran without error" isn't the same as "answered the
  ambiguity well" — this eval doesn't judge that.
- **Single run, not repeated trials.** LLM output at `temperature=0`
  should be near-deterministic, but embeddings/model behavior can still
  drift slightly run to run; no variance/confidence-interval analysis was
  done across repeated runs.
