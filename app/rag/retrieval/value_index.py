"""
Value-aware lookup: an index of actual column values (a customer's name, a
product name, an order status, ...) built fresh from the live database each
time a RagSession loads.

Schema-level retrieval (retriever.py) only ever compares a question
against SCHEMA text -- table/column descriptions, business terms -- so it
has no way to notice that a question mentions a specific record. This
module is what does: if a question literally contains "Kai Patel" and
that's a real value in customers.name, the customers table gets
force-included regardless of its retrieval score. This exists because
tuning top_k/weights can't fix that class of miss -- a proper noun matches
nothing in the schema vocabulary no matter how the ranking is weighted.

Unlike embeddings, building this index makes no external API call -- it's
just a handful of local DISTINCT queries -- so it's rebuilt fresh every
time a RagSession loads rather than cached to disk. That also sidesteps a
real staleness problem an on-disk cache would have: this index depends on
the actual DATA (which can change any time, e.g. a new customer signing
up), not just the schema or business metadata the embedding cache's
hash-based staleness check is built around.

Which columns get indexed is decided automatically from column name + type
+ cardinality, not hand-tagged in YAML, matching how physical_schema.py
itself works:
  - any column literally named "name" or ending in "_name" (products.
    product_name, categories.name, ...) -- these are almost always the
    human-readable identifier for a row, exactly what a question is likely
    to mention
  - any other text column whose distinct value count is small (a
    categorical/enum-like column: status, channel, carrier, reason, ...)
A column is skipped if it has more distinct values than its cap allows --
avoids indexing high-cardinality free text (an email address, say) that
wouldn't be useful for this kind of matching anyway.

Matching is deliberately simple: case-insensitive substring containment,
not fuzzy or stemmed. "Kai Patel" in the question matches the indexed
value "kai patel" exactly; "Kai Patell" (misspelled) or "patel, kai"
would not. That's a real limitation, not an oversight -- see the module's
use in app/rag/pipeline.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.rag.catalog.models import TableMetadata
from app.rag.catalog.physical_schema import SchemaLoader

_TEXT_TYPES = {"VARCHAR", "TEXT", "CHAR", "STRING"}

# "_name"-pattern columns are indexed generously -- they're expected to be
# near-unique identifiers (a customer's full name, say), not categorical.
MAX_NAME_COLUMN_VALUES = 2000

# Any other text column is only indexed if it looks genuinely categorical
# (status, channel, carrier, ...); anything with more distinct values than
# this is assumed to be free text, not worth indexing this way.
MAX_CATEGORICAL_DISTINCT_VALUES = 60

# Indexed values shorter than this are skipped -- avoids common short
# tokens ("no", "US", a single letter) matching almost any question.
MIN_VALUE_LENGTH = 3


@dataclass
class ValueMatch:
    table_name: str
    column_name: str
    value: str  # the indexed (lowercased) value that matched


@dataclass
class ValueIndex:
    """value.lower() -> every (table, column) it was found in."""

    entries: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def match(self, question: str) -> list[ValueMatch]:
        """Every indexed value that appears verbatim (case-insensitive) in
        the question. Longest values are checked first so a multi-word
        value like "kai patel" is matched as itself rather than the
        question only ever being checked against shorter, unrelated ones."""
        question_lower = question.lower()
        matches = []
        for value in sorted(self.entries, key=len, reverse=True):
            if value in question_lower:
                for table_name, column_name in self.entries[value]:
                    matches.append(ValueMatch(table_name=table_name, column_name=column_name, value=value))
        return matches

    def table_names(self, question: str) -> list[str]:
        """Convenience: distinct table names any value matched in, in first-matched order."""
        seen: list[str] = []
        for m in self.match(question):
            if m.table_name not in seen:
                seen.append(m.table_name)
        return seen


def _is_name_column(column_name: str) -> bool:
    return column_name == "name" or column_name.endswith("_name")


def build_value_index(loader: SchemaLoader, tables: dict[str, TableMetadata]) -> ValueIndex:
    """Builds a fresh ValueIndex by querying the live database -- see the
    module docstring for why this isn't cached to disk."""
    entries: dict[str, list[tuple[str, str]]] = {}

    for table in tables.values():
        for col in table.columns:
            if col.data_type.upper() not in _TEXT_TYPES:
                continue
            cap = MAX_NAME_COLUMN_VALUES if _is_name_column(col.name) else MAX_CATEGORICAL_DISTINCT_VALUES

            values = loader.distinct_values(table.table_name, col.name, max_values=cap)
            if not values:
                continue

            for raw_value in values:
                value = str(raw_value).strip()
                if len(value) < MIN_VALUE_LENGTH:
                    continue
                entries.setdefault(value.lower(), []).append((table.table_name, col.name))

    return ValueIndex(entries=entries)
