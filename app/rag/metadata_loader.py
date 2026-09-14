"""
Loads business metadata (table/column descriptions, business terms,
synonyms, metric definitions) from human-editable YAML and merges it onto
the physical TableMetadata objects schema_loader produces.

Business metadata enriches physical metadata; it must never duplicate it —
there is no column "type" here, because the database already knows that.

A business-metadata reference to a table or column that doesn't physically
exist is a warning, not a hard failure: the database stays the source of
truth for what exists, and metadata authors may be describing a column
that's been renamed or not yet added. Malformed/unreadable YAML, on the
other hand, is a hard failure — it prevents correct operation.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.rag.models import DomainMetadata, MetricDefinition, TableMetadata

logger = logging.getLogger(__name__)


class MetadataError(Exception):
    """Raised for business-metadata problems that prevent correct operation."""


def load_business_metadata(metadata_file: str | Path) -> dict:
    """Parses and structurally validates a domain's YAML metadata file."""
    path = Path(metadata_file)
    if not path.exists():
        raise MetadataError(f"Metadata file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise MetadataError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(raw, dict):
        raise MetadataError(f"{path} must contain a mapping at the top level.")

    if "schema_name" not in raw:
        raise MetadataError(f"{path} is missing required key 'schema_name'.")

    tables = raw.get("tables")
    if tables is not None and not isinstance(tables, dict):
        raise MetadataError(f"{path}: 'tables' must be a mapping of table name -> definition.")

    metrics = raw.get("metrics")
    if metrics is not None and not isinstance(metrics, dict):
        raise MetadataError(f"{path}: 'metrics' must be a mapping of metric name -> definition.")

    return raw


def merge_metadata(
    domain_name: str,
    database_schema: str,
    physical_tables: list[TableMetadata],
    business: dict,
) -> DomainMetadata:
    """
    Enriches physically-discovered tables in place with business metadata
    and returns the assembled DomainMetadata. `physical_tables` should come
    straight from a SchemaLoader — untouched by any YAML.
    """
    physical_by_name = {t.table_name: t for t in physical_tables}

    for table_name, table_def in (business.get("tables") or {}).items():
        table = physical_by_name.get(table_name)
        if table is None:
            logger.warning(
                "Metadata for domain '%s' references table '%s', which does not exist "
                "in the database; skipping.",
                domain_name, table_name,
            )
            continue
        _apply_table_metadata(table, table_def or {}, domain_name)

    metrics = {
        metric_name: MetricDefinition(
            name=metric_name,
            description=(metric_def or {}).get("description"),
            synonyms=list((metric_def or {}).get("synonyms") or []),
        )
        for metric_name, metric_def in (business.get("metrics") or {}).items()
    }

    return DomainMetadata(
        name=domain_name,
        database_schema=database_schema,
        tables=physical_by_name,
        description=business.get("description"),
        business_terms=list(business.get("domain_business_terms") or []),
        metrics=metrics,
    )


def _apply_table_metadata(table: TableMetadata, table_def: dict, domain_name: str) -> None:
    table.description = table_def.get("description", table.description)
    table.business_terms = list(table_def.get("business_terms") or [])

    columns_by_name = {c.name: c for c in table.columns}
    for col_name, col_def in (table_def.get("columns") or {}).items():
        column = columns_by_name.get(col_name)
        if column is None:
            logger.warning(
                "Metadata for domain '%s' references column '%s.%s', which does not exist; "
                "skipping.",
                domain_name, table.table_name, col_name,
            )
            continue
        col_def = col_def or {}
        column.description = col_def.get("description", column.description)
        column.business_terms = list(col_def.get("business_terms") or [])
