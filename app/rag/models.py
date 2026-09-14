"""
Typed, domain-agnostic representations of schema metadata.

These are deliberately decoupled from both the raw database-introspection
format and the YAML business-metadata format: schema_loader and
metadata_loader each produce/consume these, but neither format leaks into
the other, and nothing here knows about any particular domain, table, or
column name.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ColumnMetadata:
    """A single column, physical facts plus optional business enrichment."""

    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    description: str | None = None
    business_terms: list[str] = field(default_factory=list)


@dataclass
class ForeignKey:
    column: str
    references_table: str
    references_column: str
    references_schema: str | None = None


@dataclass
class TableMetadata:
    """A single table, physical facts plus optional business enrichment."""

    schema_name: str
    table_name: str
    columns: list[ColumnMetadata] = field(default_factory=list)
    primary_keys: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    description: str | None = None
    business_terms: list[str] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def column(self, name: str) -> ColumnMetadata | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class MetricDefinition:
    """A named business metric (e.g. "revenue"), independent of any one table."""

    name: str
    description: str | None = None
    synonyms: list[str] = field(default_factory=list)


@dataclass
class DomainMetadata:
    """A fully merged (physical + business) domain, ready for document generation."""

    name: str
    database_schema: str
    tables: dict[str, TableMetadata] = field(default_factory=dict)
    description: str | None = None
    business_terms: list[str] = field(default_factory=list)
    metrics: dict[str, MetricDefinition] = field(default_factory=dict)


@dataclass
class SchemaRegistryEntry:
    """One row of the domain registry (app/metadata/schemas.yaml)."""

    name: str
    backend: str
    metadata_file: str
    database_schema: str = "main"
    database_path: str | None = None
    description: str | None = None
