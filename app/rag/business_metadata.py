"""
The business-layer half of the RAG metadata pipeline: everything that
comes from human-edited YAML rather than the database itself.

Two things live here, kept in one file because they're really one concern
(configuring a domain) at two levels:

  - the domain REGISTRY (app/metadata/schemas.yaml): which domains exist,
    and where each one's database and metadata file are
  - loading + merging one domain's business METADATA (app/metadata/<domain>.yaml)
    onto the physical schema physical_schema.py discovered

Business metadata enriches physical metadata; it must never duplicate it —
there is no column "type" here, because the database already knows that.
A business-metadata reference to a table or column that doesn't physically
exist is a warning, not a hard failure: the database stays the source of
truth for what exists. Malformed/unreadable YAML, or an unregistered
domain, is a hard failure — those prevent correct operation.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.rag.models import DomainMetadata, MetricDefinition, SchemaRegistryEntry, TableMetadata

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent  # .../app
REPO_ROOT = APP_DIR.parent
DEFAULT_REGISTRY_FILE = APP_DIR / "metadata" / "schemas.yaml"


# --------------------------------------------------------------------------
# Domain registry (app/metadata/schemas.yaml)
# --------------------------------------------------------------------------


class RegistryError(Exception):
    """Raised for problems loading or looking up the domain registry."""


class DomainRegistry:
    """
    The single place that maps a domain name (e.g. "ecommerce") to where
    its physical database lives and where its business metadata file is.
    Nothing outside this class — and no retrieval code at all — should
    hardcode that mapping.

    Adding a new domain means adding an entry to app/metadata/schemas.yaml
    plus a metadata file; nothing here needs to change.
    """

    def __init__(self, entries: dict[str, SchemaRegistryEntry], app_dir: Path, repo_root: Path):
        self._entries = entries
        self._app_dir = app_dir
        self._repo_root = repo_root

    def get(self, domain_name: str) -> SchemaRegistryEntry:
        try:
            return self._entries[domain_name]
        except KeyError:
            known = ", ".join(sorted(self._entries)) or "(none registered)"
            raise RegistryError(f"Unknown domain '{domain_name}'. Registered domains: {known}") from None

    def list_domains(self) -> list[str]:
        return sorted(self._entries)

    def metadata_path(self, domain_name: str) -> Path:
        """Path to the domain's business-metadata YAML, relative to app/."""
        entry = self.get(domain_name)
        return self._app_dir / entry.metadata_file

    def database_path(self, domain_name: str) -> Path:
        """Path to the domain's database file, relative to the repo root."""
        entry = self.get(domain_name)
        if not entry.database_path:
            raise RegistryError(f"Domain '{domain_name}' has no database_path configured.")
        return self._repo_root / entry.database_path


def load_domain_registry(registry_file: str | Path = DEFAULT_REGISTRY_FILE) -> DomainRegistry:
    path = Path(registry_file)
    if not path.exists():
        raise RegistryError(f"Domain registry file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    schemas = raw.get("schemas")
    if not isinstance(schemas, dict) or not schemas:
        raise RegistryError(f"{path} must define a non-empty 'schemas' mapping.")

    entries: dict[str, SchemaRegistryEntry] = {}
    for name, cfg in schemas.items():
        cfg = cfg or {}
        if "metadata_file" not in cfg:
            raise RegistryError(f"Registry entry '{name}' is missing required key 'metadata_file'.")
        entries[name] = SchemaRegistryEntry(
            name=name,
            backend=cfg.get("backend", "sqlite"),
            metadata_file=cfg["metadata_file"],
            database_schema=cfg.get("database_schema", "main"),
            database_path=cfg.get("database_path"),
            description=cfg.get("description"),
        )

    return DomainRegistry(entries, APP_DIR, REPO_ROOT)


# --------------------------------------------------------------------------
# Business metadata (app/metadata/<domain>.yaml)
# --------------------------------------------------------------------------


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
    straight from a physical_schema loader — untouched by any YAML.
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
            description=_clean_text((metric_def or {}).get("description")),
            synonyms=list((metric_def or {}).get("synonyms") or []),
        )
        for metric_name, metric_def in (business.get("metrics") or {}).items()
    }

    return DomainMetadata(
        name=domain_name,
        database_schema=database_schema,
        tables=physical_by_name,
        description=_clean_text(business.get("description")),
        business_terms=list(business.get("domain_business_terms") or []),
        metrics=metrics,
    )


def _clean_text(value: str | None) -> str | None:
    """
    Collapses a YAML folded (">") scalar back to one line and drops the
    trailing newline block-scalar style adds, so descriptions never carry
    stray whitespace into rendered documents or prompts.
    """
    if value is None:
        return None
    return " ".join(value.split())


def _apply_table_metadata(table: TableMetadata, table_def: dict, domain_name: str) -> None:
    table.description = _clean_text(table_def.get("description")) or table.description
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
        column.description = _clean_text(col_def.get("description")) or column.description
        column.business_terms = list(col_def.get("business_terms") or [])
