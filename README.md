# NL-to-SQL Chatbot

Ask questions in plain English, get them turned into SQL, run safely against
a database, and get results back — with a plain-English summary, a headline
figure, and a chart. Ships with a small web UI and a JSON API.

## How it works

```
question --> [schema introspection] --> [LLM generates SQL]
         --> [safety validation: SELECT-only, no stacked statements]
         --> [execute against DB, capped rows] --> results + summary
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Add your OpenAI API key

```bash
cp .env.example .env
```

Then edit `.env` and set `OPENAI_API_KEY=sk-...` (get a key at
https://platform.openai.com/api-keys). The app loads `.env` automatically on
startup via `python-dotenv`; a plain `export OPENAI_API_KEY=...` works too.

Optional: set `SQL_MODEL` / `SUMMARY_MODEL` in `.env` to use a different model
(default `gpt-4o-mini`).

### 3. Create the demo database

```bash
python create_demo_db.py
```

This writes `demo.db` — a small customers / products / orders / order_items
schema with ~120 sample orders — so there's something to query immediately.

### 4. Run

```bash
uvicorn app.main:app --reload
```

- **Web UI:** http://localhost:8000/
- **Interactive API docs:** http://localhost:8000/docs

## Web UI

A two-pane workspace: the conversation on the left, the answer on the right.
Each answer shows the model's summary, a large headline figure, a sorted bar
chart, the full result table, and the generated SQL (collapsed). A
**Result / Data model** toggle lets you view the database schema at any time
without losing your place.

## API

| Method | Path      | Purpose                                  |
|--------|-----------|------------------------------------------|
| GET    | `/`       | Web UI                                   |
| GET    | `/docs`   | Swagger UI                               |
| GET    | `/schema` | Current database schema (text)           |
| GET    | `/health` | Liveness check                           |
| POST   | `/ask`    | Natural-language question -> SQL + rows  |

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Which 5 customers spent the most money on completed orders?"}'
```

```json
{
  "question": "Which 5 customers spent the most money on completed orders?",
  "sql": "SELECT ...",
  "rows": [...],
  "row_count": 5,
  "summary": "..."
}
```

Set `"include_summary": false` in the request to skip the summary call (roughly
halves the OpenAI cost per request).

## Deploy to Render

The repo includes [`render.yaml`](render.yaml), a Blueprint that provisions a
free web service.

1. Push to GitHub.
2. In Render: **New +** -> **Blueprint** -> select this repo -> **Apply**.
3. When prompted, set `OPENAI_API_KEY` (it is `sync: false`, so Render never
   reads it from the repo).

What the Blueprint runs:

| Step  | Command |
|-------|---------|
| Build | `pip install -r requirements.txt && python create_demo_db.py` |
| Start | `gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:$PORT --timeout 120` |

Notes:

- **gunicorn + uvicorn worker.** Render (Linux) runs gunicorn as the process
  manager with a uvicorn ASGI worker. gunicorn does **not** run on Windows, so
  keep using `uvicorn app.main:app --reload` for local development.
- `$PORT` is injected by Render; bind to it, not a fixed port.
- `--timeout 120` gives the OpenAI round-trip room before a worker is killed.
- `demo.db` is rebuilt on every deploy (Render's disk is ephemeral). That's
  fine — the app only reads from it. To ship your own data instead, remove the
  `create_demo_db.py` step and point `app/db.py` at a managed database.
- Prefer plain uvicorn? Swap the start command for
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT` and drop `gunicorn` from
  `requirements.txt`.

## Swapping in your own database

Everything in `app/db.py` is written for SQLite, but the pattern generalizes:

- **Postgres**: use `psycopg2` / `asyncpg` for the connection; introspect via
  `information_schema.columns` and `information_schema.table_constraints`
  instead of `PRAGMA table_info` / `PRAGMA foreign_key_list`.
- **MySQL**: similar — `information_schema` again, with `pymysql` or
  `mysql-connector-python`.

The safety validation (`validate_sql`), row capping, and FastAPI layer don't
need to change.

## Safety notes

This is a starting point, not a production-hardened system. Before pointing it
at a real database, also consider:

- **Least-privilege DB user**: connect with a role that only has `SELECT`
  grants — don't rely on the regex validator as your only defense.
- **Row-level security / column masking** if some data shouldn't be queryable
  by all users.
- **Query cost limits**: a syntactically safe `SELECT` can still be a full
  table scan. Consider `EXPLAIN`-ing first or setting a statement timeout.
- **Rate limiting** on the `/ask` endpoint.
- **Logging generated SQL** for auditing.

## Model quality tips

- Keep the schema description concise and accurate — avoid dumping unrelated
  tables into the prompt.
- If generated SQL is subtly wrong (wrong join, wrong aggregation), add a few
  example question -> SQL pairs to the system prompt (few-shot).
- For a bigger schema, retrieve only the relevant tables per question (schema
  RAG) instead of sending the whole schema every time.
- `gpt-4o-mini` is a solid default; upgrade to `gpt-4o` for complex multi-join
  queries if you see accuracy issues.

## Cost note

Every `/ask` call makes 1–2 OpenAI API calls (SQL generation, plus an optional
summary). Track usage on the OpenAI dashboard.
