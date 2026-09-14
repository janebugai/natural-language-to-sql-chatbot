"""
Compact schema context builder: turns a domain's merged metadata plus a
graph-expansion result into the text actually handed to the SQL-generation
LLM, replacing the "send the whole schema" default with just the tables
retrieval (and foreign-key expansion) decided were relevant.

Column filtering is deliberately pluggable (`column_filter`) rather than
hardcoded to "every column of a selected table" -- a future column-level
retrieval pass can plug in a filter here without touching how context is
rendered; the default simply keeps today's behavior of including all of
them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from app.rag import config
from app.rag.catalog.models import ColumnMetadata, DomainMetadata, TableMetadata
from app.rag.retrieval.schema_graph import ExpansionResult

logger = logging.getLogger(__name__)

ColumnFilter = Callable[[TableMetadata], list[ColumnMetadata]]

_WORD_RE = re.compile(r"[a-z0-9]+")


def _all_columns(table: TableMetadata) -> list[ColumnMetadata]:
    """Default column filter: include every column. See module docstring."""
    return table.columns


@dataclass
class SchemaContext:
    """The rendered context handed to the SQL-generation LLM, plus enough
    structure to log/debug what went into it without re-parsing the text."""

    text: str
    table_names: list[str]          # every table actually in the context (retrieved + bridge)
    retrieved_table_names: list[str]
    bridge_table_names: list[str]
    dropped_table_names: list[str]  # bridge tables cut to respect max_tables
    character_count: int


def build(
    domain: DomainMetadata,
    expansion: ExpansionResult,
    question: str | None = None,
    column_filter: ColumnFilter = _all_columns,
    max_tables: int | None = None,
) -> SchemaContext:
    """
    Renders the final, graph-expanded table set into compact text.

    Never drops an originally-retrieved table to respect `max_tables` --
    only bridge tables are eligible for truncation, dropped from the end
    of the bridge list (the tables expand() needed for the longest/last
    join chain), since those are the least certain to matter. If even the
    retrieved set alone exceeds max_tables, all of it is kept anyway and a
    warning is logged -- retrieval's own choices are never second-guessed
    here.

    `question` is used only to decide which domain-level metric
    definitions are "applicable" (matched against the question the same
    way retriever.py matches business terms). Without it, no metrics are
    included -- keeping the context compact by default rather than
    guessing relevance.
    """
    max_tables = config.RAG_MAX_CONTEXT_TABLES if max_tables is None else max_tables

    retrieved = list(expansion.retrieved_tables)
    bridge = list(expansion.bridge_tables)
    dropped: list[str] = []

    total = len(retrieved) + len(bridge)
    if total > max_tables:
        allowed_bridge = max(0, max_tables - len(retrieved))
        dropped = bridge[allowed_bridge:]
        bridge = bridge[:allowed_bridge]
        logger.warning(
            "Context would include %d tables (cap is %d); dropped bridge tables %s. "
            "Join paths through them may be incomplete.",
            total, max_tables, dropped,
        )

    final_tables = retrieved + bridge
    bridge_set = set(bridge)
    tables_by_name = {name: domain.tables[name] for name in final_tables if name in domain.tables}

    missing = [name for name in final_tables if name not in tables_by_name]
    if missing:
        logger.warning("Tables in the expansion result aren't in the domain's catalog, skipping: %s", missing)

    # Single-schema shortcut: only qualify table names if the selected
    # tables actually span more than one schema -- keeps the common,
    # single-domain case readable.
    qualify = len({t.schema_name for t in tables_by_name.values()}) > 1

    def _name(table: TableMetadata) -> str:
        return table.qualified_name if qualify else table.table_name

    sections: list[str] = [_render_table(tables_by_name[name], _name, name in bridge_set, column_filter)
                            for name in final_tables if name in tables_by_name]

    relationships = _render_relationships(final_tables, tables_by_name, _name)
    if relationships:
        sections.append(relationships)

    terms = _render_business_terms(final_tables, tables_by_name, _name)
    if terms:
        sections.append(terms)

    metrics = _render_metrics(domain, question)
    if metrics:
        sections.append(metrics)

    text = "\n\n".join(sections)

    return SchemaContext(
        text=text,
        table_names=final_tables,
        retrieved_table_names=retrieved,
        bridge_table_names=bridge,
        dropped_table_names=dropped,
        character_count=len(text),
    )


def _render_table(table: TableMetadata, name_of: Callable[[TableMetadata], str], is_bridge: bool,
                   column_filter: ColumnFilter) -> str:
    note = "  (included to complete a join path)" if is_bridge else ""
    lines = [f"TABLE {name_of(table)}{note}"]
    if table.description:
        lines.append(f"Description: {table.description}")

    columns = column_filter(table) or table.columns
    col_descs = []
    for col in columns:
        flags = [f for f, present in (("primary key", col.is_primary_key), ("foreign key", col.is_foreign_key)) if present]
        flag_str = f", {', '.join(flags)}" if flags else ""
        col_descs.append(f"{col.name} ({col.data_type}{flag_str})")
    lines.append("Columns: " + ", ".join(col_descs))

    return "\n".join(lines)


def _render_relationships(
    final_tables: list[str], tables_by_name: dict[str, TableMetadata], name_of: Callable[[TableMetadata], str]
) -> str | None:
    lines = []
    for table_name in final_tables:
        table = tables_by_name.get(table_name)
        if table is None:
            continue
        for fk in table.foreign_keys:
            target = tables_by_name.get(fk.references_table)
            if target is not None:
                lines.append(f"{name_of(table)}.{fk.column} -> {name_of(target)}.{fk.references_column}")
    return "Relationships:\n" + "\n".join(lines) if lines else None


def _render_business_terms(
    final_tables: list[str], tables_by_name: dict[str, TableMetadata], name_of: Callable[[TableMetadata], str]
) -> str | None:
    lines = []
    for table_name in final_tables:
        table = tables_by_name.get(table_name)
        if table and table.business_terms:
            lines.append(f"{name_of(table)}: {', '.join(table.business_terms)}")
    return "Business terminology:\n" + "\n".join(lines) if lines else None


def _render_metrics(domain: DomainMetadata, question: str | None) -> str | None:
    if not question or not domain.metrics:
        return None

    question_lower = question.lower()
    question_terms = set(_WORD_RE.findall(question_lower))

    def mentioned(term: str) -> bool:
        term = term.lower().strip()
        return (term in question_lower) if " " in term else (term in question_terms)

    lines = []
    for metric in domain.metrics.values():
        if mentioned(metric.name) or any(mentioned(s) for s in metric.synonyms):
            lines.append(f"{metric.name}: {metric.description}")
    return "Relevant business metrics:\n" + "\n".join(lines) if lines else None
