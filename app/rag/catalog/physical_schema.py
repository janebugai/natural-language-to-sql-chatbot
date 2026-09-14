"""
Database schema introspection.

Produces physical TableMetadata objects — column names, types, nullability,
primary keys, foreign keys — purely from what the database itself reports.
Nothing here assumes a particular domain's table or column names, so the
same loader works unchanged for any schema registered in
app/metadata/schemas.yaml.

Two backends today — SQLite (legacy) and DuckDB (what the app actually runs
on, see app/db.py) — both implementing the same SchemaLoader interface.
Postgres could be added later the same way; nothing downstream (retriever,
schema_graph, context_builder) would need to change.
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

import duckdb

from app.rag.catalog.models import ColumnMetadata, ForeignKey, SchemaRegistryEntry, TableMetadata


class SchemaLoader(ABC):
    """Interface for discovering physical schema metadata from a database."""

    @abstractmethod
    def load_tables(self, database_schema: str) -> list[TableMetadata]:
        """Return physical metadata for every table visible in the given schema/namespace."""

    @abstractmethod
    def distinct_values(self, table_name: str, column_name: str, max_values: int) -> list[str] | None:
        """
        Up to `max_values` distinct, non-null values of one column, or None
        if the column has more distinct values than that (too high-
        cardinality to be useful -- e.g. a free-text column). Used by
        app/rag/retrieval/value_index.py to notice when a question mentions
        a specific record (a customer's name, a product name, ...) that
        schema-level retrieval has no way to see.
        """


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

    def distinct_values(self, table_name: str, column_name: str, max_values: int) -> list[str] | None:
        conn = sqlite3.connect(self.database_path)
        try:
            cur = conn.cursor()
            cur.execute(f'SELECT COUNT(DISTINCT "{column_name}") FROM "{table_name}"')
            if cur.fetchone()[0] > max_values:
                return None
            cur.execute(f'SELECT DISTINCT "{column_name}" FROM "{table_name}" WHERE "{column_name}" IS NOT NULL')
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()


class DuckDBSchemaLoader(SchemaLoader):
    """
    Introspects a DuckDB database file via its duckdb_columns()/
    duckdb_constraints() metadata functions (DuckDB's equivalent of
    SQLite's PRAGMAs / Postgres's information_schema).

    Tables matching `hidden_table_pattern` (a SQL LIKE pattern) are excluded
    — this app's dbt project leaves its raw seed tables (raw_categories,
    raw_orders, ...) in the same database as the marts it builds, and those
    are a build detail, not part of the schema the app or an LLM should see.
    """

    def __init__(self, database_path: str | Path, hidden_table_pattern: str = "raw_%"):
        self.database_path = Path(database_path)
        self.hidden_table_pattern = hidden_table_pattern

    def load_tables(self, database_schema: str = "main") -> list[TableMetadata]:
        if not self.database_path.exists():
            raise FileNotFoundError(f"DuckDB database not found: {self.database_path}")

        conn = duckdb.connect(str(self.database_path), read_only=True)
        try:
            table_names = [
                row[0]
                for row in conn.execute(
                    "select table_name from duckdb_tables() "
                    "where schema_name = ? and not internal and table_name not like ? "
                    "order by table_name",
                    [database_schema, self.hidden_table_pattern],
                ).fetchall()
            ]
            return [self._load_table(conn, database_schema, name) for name in table_names]
        finally:
            conn.close()

    def _load_table(
        self, conn: duckdb.DuckDBPyConnection, database_schema: str, table_name: str
    ) -> TableMetadata:
        col_rows = conn.execute(
            "select column_name, data_type, is_nullable from duckdb_columns() "
            "where schema_name = ? and table_name = ? order by column_index",
            [database_schema, table_name],
        ).fetchall()

        constraint_rows = conn.execute(
            "select constraint_type, constraint_column_names, referenced_table, "
            "referenced_column_names from duckdb_constraints() "
            "where schema_name = ? and table_name = ? "
            "and constraint_type in ('PRIMARY KEY', 'FOREIGN KEY')",
            [database_schema, table_name],
        ).fetchall()

        primary_keys: list[str] = []
        foreign_keys: list[ForeignKey] = []
        for constraint_type, column_names, ref_table, ref_columns in constraint_rows:
            if constraint_type == "PRIMARY KEY":
                primary_keys.extend(column_names)
            elif constraint_type == "FOREIGN KEY":
                # DuckDB supports composite FKs; this app only has
                # single-column ones, so pair them up positionally.
                for column_name, ref_column in zip(column_names, ref_columns):
                    foreign_keys.append(
                        ForeignKey(
                            column=column_name,
                            references_table=ref_table,
                            references_column=ref_column,
                            references_schema=database_schema,
                        )
                    )

        primary_key_set = set(primary_keys)
        fk_columns = {fk.column for fk in foreign_keys}

        columns = [
            ColumnMetadata(
                name=name,
                data_type=data_type,
                nullable=(is_nullable == "YES" if isinstance(is_nullable, str) else bool(is_nullable)),
                is_primary_key=name in primary_key_set,
                is_foreign_key=name in fk_columns,
            )
            for name, data_type, is_nullable in col_rows
        ]

        return TableMetadata(
            schema_name=database_schema,
            table_name=table_name,
            columns=columns,
            primary_keys=primary_keys,
            foreign_keys=foreign_keys,
        )

    def distinct_values(self, table_name: str, column_name: str, max_values: int) -> list[str] | None:
        conn = duckdb.connect(str(self.database_path), read_only=True)
        try:
            count = conn.execute(f'SELECT COUNT(DISTINCT "{column_name}") FROM "{table_name}"').fetchone()[0]
            if count > max_values:
                return None
            rows = conn.execute(
                f'SELECT DISTINCT "{column_name}" FROM "{table_name}" WHERE "{column_name}" IS NOT NULL'
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()


_LOADER_FACTORIES = {
    "sqlite": SQLiteSchemaLoader,
    "duckdb": DuckDBSchemaLoader,
}


def build_schema_loader(entry: SchemaRegistryEntry, resolved_db_path: str | Path) -> SchemaLoader:
    """
    Picks the right SchemaLoader for a registry entry's backend. This is the
    only place that dispatches on `backend` — nothing else should need an
    if/else on it.
    """
    try:
        loader_cls = _LOADER_FACTORIES[entry.backend]
    except KeyError:
        known = ", ".join(sorted(_LOADER_FACTORIES))
        raise ValueError(f"Unknown backend '{entry.backend}' for domain '{entry.name}'. Supported: {known}.")
    return loader_cls(resolved_db_path)
