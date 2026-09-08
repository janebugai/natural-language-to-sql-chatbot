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
write a single SQLite SELECT query that answers the question.

Rules:
- Only output the SQL query, nothing else. No explanation, no markdown fences.
- Only use SELECT statements — never modify data.
- Use only the tables and columns listed in the schema.
- When a query joins more than one table, give every table a short alias and
  qualify EVERY column reference with it (in SELECT, JOIN, WHERE, GROUP BY,
  ORDER BY, and HAVING). Column names like product_id, customer_id and
  order_id appear in several tables and are ambiguous unqualified.
- When the question asks about an entity (product, customer, category,
  carrier...), return its human-readable name column, not just its id.
- Make text filters case-insensitive: compare with LOWER(column) = LOWER('value'),
  or use LIKE with a COLLATE NOCASE clause. Never rely on an exact-case match for
  values a user typed (names, cities, statuses, categories, etc.).
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
