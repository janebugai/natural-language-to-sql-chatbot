"""
Database schema introspection.

Produces physical TableMetadata objects — column names, types, nullability,
primary keys, foreign keys — purely from what the database itself reports.
Nothing here assumes a particular domain's table or column names, so the
same loader works unchanged for any schema registered in
app/metadata/schemas.yaml.

Implemented against SQLite today, matching the rest of the app (see
app/db.py). Postgres support can be added later as a second class
implementing the same SchemaLoader interface — retriever.py,
schema_graph.py, etc. would not need to change.
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from app.rag.models import ColumnMetadata, ForeignKey, TableMetadata


class SchemaLoader(ABC):
    """Interface for discovering physical schema metadata from a database."""

    @abstractmethod
    def load_tables(self, database_schema: str) -> list[TableMetadata]:
        """Return physical metadata for every table visible in the given schema/namespace."""


class SQLiteSchemaLoader(SchemaLoader):
    """Introspects a SQLite database file via PRAGMA statements."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def load_tables(self, database_schema: str = "main") -> list[TableMetadata]:
        """
        Discover every user table in the database file.

        `database_schema` is accepted for interface parity with backends
        that have real namespaces (e.g. Postgres schemas); SQLite has a
        single implicit schema per file, conventionally named "main", and
        the value is only used as a label on the returned TableMetadata.
        """
        if not self.database_path.exists():
            raise FileNotFoundError(f"SQLite database not found: {self.database_path}")

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
            table_names = [row["name"] for row in cur.fetchall()]
            return [self._load_table(cur, database_schema, name) for name in table_names]
        finally:
            conn.close()

    def _load_table(self, cur: sqlite3.Cursor, database_schema: str, table_name: str) -> TableMetadata:
        cur.execute(f'PRAGMA table_info("{table_name}")')
        col_rows = cur.fetchall()

        cur.execute(f'PRAGMA foreign_key_list("{table_name}")')
        foreign_keys = [
            ForeignKey(
                column=fk["from"],
                references_table=fk["table"],
                references_column=fk["to"],
            )
            for fk in cur.fetchall()
        ]
        fk_columns = {fk.column for fk in foreign_keys}

        columns = [
            ColumnMetadata(
                name=c["name"],
                data_type=c["type"] or "unknown",
                nullable=not bool(c["notnull"]),
                is_primary_key=bool(c["pk"]),
                is_foreign_key=c["name"] in fk_columns,
            )
            for c in col_rows
        ]
        primary_keys = [c.name for c in columns if c.is_primary_key]

        return TableMetadata(
            schema_name=database_schema,
            table_name=table_name,
            columns=columns,
            primary_keys=primary_keys,
            foreign_keys=foreign_keys,
        )
