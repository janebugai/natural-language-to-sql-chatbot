"""
Talks to the OpenAI API and turns a natural-language question + schema
description into a single SQL query.

Requires the OPENAI_API_KEY environment variable to be set (see .env.example):
    export OPENAI_API_KEY="sk-..."
"""
import os
import re
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Overridable via env (SQL_MODEL / SUMMARY_MODEL); gpt-4o-mini is a solid default.
SQL_MODEL = os.environ.get("SQL_MODEL", "gpt-4o-mini")
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = """You are a SQL expert. Given a database schema and a question,
write a single DuckDB SQL SELECT query that answers the question.

The schema below may be the full database or a relevant subset chosen for
this question — never assume a table or column exists just because it
would make sense; only what's actually listed is real.

Rules:
- Use only the provided schema context. Do not invent tables. Do not
  invent columns. If a "Relationships" section is given, use those exact
  join paths — do not guess a different join between tables.
- Generate exactly one read-only SELECT statement. Do not generate INSERT,
  UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, MERGE, COPY, ATTACH, or
  any other write/DDL operation.
- Return SQL only — no explanation, no markdown fences.
- When a query joins more than one table, give every table a short alias and
  qualify EVERY column reference with it (in SELECT, JOIN, WHERE, GROUP BY,
  ORDER BY, and HAVING). Column names like product_id, customer_id and
  order_id appear in several tables and are ambiguous unqualified.
- When the question asks about an entity (product, customer, category,
  carrier...), return its human-readable name column, not just its id.
- When a query uses GROUP BY (including inside a subquery), every selected
  column that is not wrapped in an aggregate function (SUM, AVG, COUNT,
  MIN, MAX, ...) MUST appear in that same GROUP BY clause. This applies
  even to a column that seems "obviously" constant per group — e.g.
  grouping by an order's id does NOT exempt that order's other columns
  (shipping_cost, tax, ...) from needing to be listed too. DuckDB enforces
  this strictly and will reject the query otherwise. Never nest one
  aggregate function inside another (e.g. AVG(x + SUM(y)) is invalid) —
  aggregate the inner value in a subquery or CTE first, then aggregate
  that result in the outer query.
- Make text filters case-insensitive: compare with LOWER(column) = LOWER('value'),
  or use ILIKE. Never rely on an exact-case match for values a user typed
  (names, cities, statuses, categories, etc.).
- If the question cannot be answered with the given schema, output:
  SELECT 'Cannot answer with available schema' AS error
"""


def _extract_sql(raw_text: str) -> str:
    """Strips markdown fences and surrounding chatter the model might add."""
    text = raw_text.strip()
    fence_match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()
    select_match = re.search(r"(SELECT|WITH)\b.*", text, re.DOTALL | re.IGNORECASE)
    if select_match:
        text = select_match.group(0).strip()
    return text.rstrip(";").strip()


def generate_sql(question: str, schema: str) -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")

    response = client.chat.completions.create(
        model=SQL_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"### Schema\n{schema}\n\n### Question\n{question}\n\n### SQL Query"},
        ],
    )
    raw = response.choices[0].message.content or ""
    return _extract_sql(raw)


def repair_sql(question: str, schema: str, bad_sql: str, error: str) -> str:
    """Give the model its failed query plus the DB error and ask for one fix."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY environment variable is not set.")

    response = client.chat.completions.create(
        model=SQL_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"### Schema\n{schema}\n\n### Question\n{question}\n\n### SQL Query"},
            {"role": "assistant", "content": bad_sql},
            {"role": "user", "content": (
                f"That query failed with this database error:\n{error}\n\n"
                "Return a corrected single SELECT query. Alias every table and "
                "qualify every column reference. Output only the SQL."
            )},
        ],
    )
    return _extract_sql(response.choices[0].message.content or "")


def summarize_results(question: str, rows: list[dict]) -> str:
    """Optional: ask the model to describe the results in plain English."""
    if not rows:
        return "No results were found for that query."

    preview = str(rows[:10])
    response = client.chat.completions.create(
        model=SUMMARY_MODEL,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": "Summarize query results in 1-2 plain-English sentences. "
                            "Do not repeat raw data, just describe the finding.",
            },
            {"role": "user", "content": f"Question: {question}\nResults (first 10 rows): {preview}"},
        ],
    )
    return (response.choices[0].message.content or "").strip()
