"""
Schema-aware RAG package.

Turns a natural-language question into a compact, relevant slice of schema
context (rather than the entire database schema) for SQL generation.

    config.py              every tunable RAG setting, in one place

    catalog/                what's in the database, physically and by business meaning
        models.py               typed dataclasses shared across the package
        physical_schema.py      physical schema discovery (SQLite / DuckDB)
        business_metadata.py    domain registry + business metadata (YAML), merged onto physical schema

    retrieval/               turning a question into the relevant slice of the catalog
        schema_documents.py     deterministic per-table retrieval documents
        embeddings.py            embedding generation/caching + similarity
        retriever.py             hybrid semantic + keyword ranking
        schema_graph.py          foreign-key graph + bridge-table expansion

    context_builder.py     compact prompt context for the SQL-generation LLM
    refresh.py              CLI to (re)build documents/embeddings for a domain
"""
