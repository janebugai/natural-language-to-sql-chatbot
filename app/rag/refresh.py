"""
CLI to (re)build a domain's schema documents and embeddings.

    python -m app.rag.refresh --schema ecommerce
    python -m app.rag.refresh --all

For each domain refreshed, this:
    1. inspects the physical schema (catalog/physical_schema.py)
    2. loads business metadata and merges it on (catalog/business_metadata.py)
    3. builds deterministic schema documents (retrieval/schema_documents.py)
    4. embeds them, reusing cached vectors for any document whose content
       hash hasn't changed (retrieval/embeddings.py) -- an OpenAI call is
       only made for tables whose rendered document actually changed
    5. builds the foreign-key graph, purely to report how many
       relationships were discovered (retrieval/schema_graph.py)

Nothing here does retrieval -- this only rebuilds the derived state
retrieval reads at request time. It requires OPENAI_API_KEY to be set
whenever a document actually needs (re-)embedding; refreshing a domain
where nothing changed since the last run makes no API calls at all.
"""
from __future__ import annotations

import argparse
import sys

from app.rag.catalog.business_metadata import (
    DomainRegistry,
    MetadataError,
    RegistryError,
    load_business_metadata,
    load_domain_registry,
    merge_metadata,
)
from app.rag.catalog.physical_schema import build_schema_loader
from app.rag.retrieval.embeddings import OpenAIEmbeddingProvider, embed_documents
from app.rag.retrieval.schema_documents import build_documents
from app.rag.retrieval.schema_graph import build_graph


class RefreshError(Exception):
    """Raised when a domain fails to refresh; caught at the CLI boundary
    so one bad domain doesn't take down a --all run or print a traceback."""


def refresh_domain(domain_name: str, registry: DomainRegistry) -> None:
    """Refreshes one domain end-to-end and prints its summary."""
    try:
        entry = registry.get(domain_name)
        loader = build_schema_loader(entry, registry.database_path(domain_name))
        physical_tables = loader.load_tables(entry.database_schema)
        if not physical_tables:
            raise RefreshError(f"No tables discovered for domain '{domain_name}' -- is the database empty?")

        business = load_business_metadata(registry.metadata_path(domain_name))
        domain = merge_metadata(domain_name, entry.database_schema, physical_tables, business)

        documents = build_documents(domain_name, domain.tables)
        embedded, stats = embed_documents(domain_name, documents, provider=OpenAIEmbeddingProvider())
        graph = build_graph(domain.tables)
    except RefreshError:
        raise
    except (RegistryError, MetadataError, FileNotFoundError) as e:
        raise RefreshError(str(e)) from e
    except Exception as e:  # embedding API errors, network errors, etc. -- not enumerable ahead of time
        raise RefreshError(f"unexpected error: {e}") from e

    print(f"Schema: {domain_name}")
    print()
    print(f"Tables discovered: {len(physical_tables)}")
    print(f"Documents created: {len(documents)}")
    print(f"Embeddings reused: {stats.reused}")
    print(f"Embeddings regenerated: {stats.regenerated}")
    print(f"Relationships discovered: {len(graph.edges)}")
    print()
    print("Refresh complete.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.rag.refresh",
        description="Rebuild schema documents and embeddings for one or all registered domains.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--schema", metavar="DOMAIN", help="Refresh a single domain (e.g. ecommerce).")
    group.add_argument("--all", action="store_true", help="Refresh every domain in the registry.")
    args = parser.parse_args(argv)

    try:
        registry = load_domain_registry()
    except RegistryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    domains = registry.list_domains() if args.all else [args.schema]

    exit_code = 0
    for i, domain_name in enumerate(domains):
        if i > 0:
            print()
        try:
            refresh_domain(domain_name, registry)
        except RefreshError as e:
            print(f"Error refreshing '{domain_name}': {e}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
