"""
Domain/schema registry: the single place that maps a domain name (e.g.
"ecommerce") to where its physical database lives and where its business
metadata file is. Nothing outside this module — and no retrieval code at
all — should hardcode that mapping.

Adding a new domain means adding an entry to app/metadata/schemas.yaml plus
a metadata file; nothing here needs to change.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.rag.models import SchemaRegistryEntry

APP_DIR = Path(__file__).resolve().parent.parent  # .../app
REPO_ROOT = APP_DIR.parent
DEFAULT_REGISTRY_FILE = APP_DIR / "metadata" / "schemas.yaml"


class RegistryError(Exception):
    """Raised for problems loading or looking up the domain registry."""


class SchemaRegistry:
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
        """Path to the domain's SQLite file, relative to the repo root."""
        entry = self.get(domain_name)
        if not entry.database_path:
            raise RegistryError(f"Domain '{domain_name}' has no database_path configured.")
        return self._repo_root / entry.database_path


def load_registry(registry_file: str | Path = DEFAULT_REGISTRY_FILE) -> SchemaRegistry:
    path = Path(registry_file)
    if not path.exists():
        raise RegistryError(f"Schema registry file not found: {path}")

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

    return SchemaRegistry(entries, APP_DIR, REPO_ROOT)
