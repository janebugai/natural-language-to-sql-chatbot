"""
Converts merged (physical + business) table metadata into deterministic
text documents suitable for embedding and keyword retrieval.

"Deterministic" means: given the same TableMetadata content, build_document()
always produces byte-identical output. That determinism is what makes the
hash-based incremental refresh in embeddings.py possible — a table whose
rendered document hasn't changed can reuse its existing embedding instead
of paying for a new one.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.rag.models import TableMetadata

# Bump this if _render()'s output shape changes. That invalidates every
# cached embedding, since the *document* a table produces would differ even
# though nothing about the underlying schema or business metadata changed.
DOCUMENT_FORMAT_VERSION = "v1"


@dataclass
class SchemaDocument:
    """A single table's retrievable text document plus its content hash."""

    domain: str
    table_name: str
    qualified_name: str
    content: str
    content_hash: str


def build_document(domain: str, table: TableMetadata) -> SchemaDocument:
    """Renders one table's metadata into a deterministic text document."""
    content = _render(table)
    return SchemaDocument(
        domain=domain,
        table_name=table.table_name,
        qualified_name=table.qualified_name,
        content=content,
        content_hash=_hash(table.table_name, content),
    )


def build_documents(domain: str, tables: dict[str, TableMetadata]) -> list[SchemaDocument]:
    """Renders every table in a domain, sorted by table name for determinism."""
    return [build_document(domain, tables[name]) for name in sorted(tables)]


def _render(table: TableMetadata) -> str:
    lines = [f"Table: {table.qualified_name}", "", "Description:", table.description or "(no description available)", "", "Columns:"]

    for col in table.columns:
        flags = []
        if col.is_primary_key:
            flags.append("primary key")
        if col.is_foreign_key:
            flags.append("foreign key")
        flag_str = f" - {', '.join(flags)}" if flags else ""
        desc = f" - {col.description}" if col.description else ""
        lines.append(f"{col.name} - {col.data_type}{flag_str}{desc}")
    lines.append("")

    if table.foreign_keys:
        lines.append("Relationships:")
        for fk in table.foreign_keys:
            ref_schema = fk.references_schema or table.schema_name
            lines.append(
                f"{table.qualified_name}.{fk.column} -> "
                f"{ref_schema}.{fk.references_table}.{fk.references_column}"
            )
        lines.append("")

    all_terms = list(table.business_terms)
    for col in table.columns:
        for term in col.business_terms:
            if term not in all_terms:
                all_terms.append(term)
    if all_terms:
        lines.append("Business terms:")
        lines.append(", ".join(all_terms))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _hash(table_name: str, content: str) -> str:
    payload = f"{table_name}\n{content}\n{DOCUMENT_FORMAT_VERSION}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
