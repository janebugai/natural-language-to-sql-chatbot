"""
Retrieval: turning a natural-language question into the relevant slice of
the catalog (app.rag.catalog).

    schema_documents.py   deterministic per-table retrieval documents, built from the catalog
    embeddings.py          embedding generation/caching + similarity
    retriever.py           hybrid semantic + keyword ranking
    schema_graph.py        foreign-key graph + bridge-table expansion
"""
