"""
The catalog: what's in the database, physically and by business meaning.

    models.py             typed dataclasses shared across the whole RAG package
    physical_schema.py    database introspection (SQLite / DuckDB)
    business_metadata.py  domain registry + business metadata (YAML), merged onto physical_schema's output

See app.rag.retrieval for turning a question into a relevant slice of
this.
"""
