"""
Foreign-key relationship graph: builds an undirected graph from the tables'
declared foreign keys, and expands a set of retrieved tables with whatever
intermediate ("bridge") tables are needed to connect them via real FK
paths -- e.g. if retrieval picks categories and returns, but the only path
between them is categories -> products -> order_items -> returns, expand()
adds products and order_items as bridge tables.

The graph is built purely from what physical_schema.py discovered; nothing here
invents a relationship that isn't a real foreign key, and nothing here
knows a table or column by name -- it only ever sees whatever
TableMetadata objects it's handed.

Known characteristic, not a bug: expansion optimizes purely for FK hop
count, not business-story naturalness. In the demo schema, connecting
"customers" and "products" bridges through "reviews" (2 hops: both have a
direct FK to it) rather than through "orders" + "order_items" (3 hops),
even though the latter is the more obviously "correct" story for most
questions. A real shortest-path search has no way to know that -- it just
counts edges. This is exactly the conservative, no-invented-relationships
behavior the design calls for; making it prefer "primary" join paths would
require weighting edges by something other than hop count, which is a
reasonable future improvement but out of scope here.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from app.rag.models import TableMetadata

logger = logging.getLogger(__name__)


@dataclass
class GraphEdge:
    """One foreign-key relationship, in its original (declared) direction."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class SchemaGraph:
    """
    Undirected adjacency over table names, built from FK edges. Connectivity
    is treated as undirected -- a question can reasonably start from either
    side of a foreign key -- but each edge retains its original direction
    and column names for anything downstream that wants to describe the
    relationship (e.g. context_builder.py).
    """

    adjacency: dict[str, set[str]] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    _edge_lookup: dict[tuple[str, str], GraphEdge] = field(default_factory=dict, repr=False)

    def neighbors(self, table: str) -> set[str]:
        return self.adjacency.get(table, set())

    def edge_between(self, a: str, b: str) -> GraphEdge | None:
        """The FK edge connecting two directly-adjacent tables, in whichever
        direction it was actually declared. None if they aren't adjacent."""
        return self._edge_lookup.get((a, b))

    def shortest_path(self, start: str, end: str) -> list[str] | None:
        """
        BFS shortest path (every FK hop costs the same). Returns None if
        the two tables aren't connected, or either isn't in the graph.
        Ties are broken deterministically by visiting neighbors in sorted
        order, so the same graph + inputs always produce the same path --
        important since callers log and report the path chosen.
        """
        if start == end:
            return [start]
        if start not in self.adjacency or end not in self.adjacency:
            return None

        visited = {start}
        queue: deque[list[str]] = deque([[start]])
        while queue:
            path = queue.popleft()
            for neighbor in sorted(self.neighbors(path[-1])):
                if neighbor in visited:
                    continue
                new_path = path + [neighbor]
                if neighbor == end:
                    return new_path
                visited.add(neighbor)
                queue.append(new_path)
        return None


@dataclass
class ExpansionResult:
    """Result of expanding a retrieved table set into a connected one."""

    retrieved_tables: list[str]                 # the original, semantically-retrieved set
    bridge_tables: list[str]                     # tables added purely to connect the retrieved set
    final_tables: list[str]                       # retrieved_tables + bridge_tables, de-duplicated
    paths_used: list[list[str]]                   # each shortest path actually used to connect components
    unreachable_pairs: list[tuple[str, str]]       # retrieved tables the graph couldn't connect at all

    @property
    def was_expanded(self) -> bool:
        return bool(self.bridge_tables)


def build_graph(tables: dict[str, TableMetadata]) -> SchemaGraph:
    """Builds the FK graph purely from what physical_schema.py discovered -- no hardcoded relationships."""
    graph = SchemaGraph()
    for table in tables.values():
        graph.adjacency.setdefault(table.table_name, set())

    for table in tables.values():
        for fk in table.foreign_keys:
            if fk.references_table not in tables:
                # FK points at a table physical_schema.py didn't load (e.g. a
                # cross-domain reference) -- skip rather than add a
                # dangling node nothing downstream can describe.
                continue
            edge = GraphEdge(
                from_table=table.table_name,
                from_column=fk.column,
                to_table=fk.references_table,
                to_column=fk.references_column,
            )
            graph.edges.append(edge)
            graph.adjacency[table.table_name].add(fk.references_table)
            graph.adjacency[fk.references_table].add(table.table_name)
            graph._edge_lookup[(table.table_name, fk.references_table)] = edge
            graph._edge_lookup.setdefault((fk.references_table, table.table_name), edge)

    return graph


def expand(graph: SchemaGraph, retrieved_tables: list[str]) -> ExpansionResult:
    """
    Connects `retrieved_tables` via the shortest real FK paths between
    them, adding whatever intermediate tables are needed as bridge tables.

    Conservative by design: it only ever adds a table that sits on an
    actual shortest path between two retrieved tables. It never adds a
    table just because it's "related" elsewhere in the schema, and it
    never invents a path where none exists -- two retrieved tables in
    disconnected parts of the graph are reported in `unreachable_pairs`
    rather than silently ignored or force-connected.

    Tables are connected incrementally in the given order (retrieval's own
    ranking, highest-scored first) -- the first table anchors a growing
    connected set, and each subsequent table joins via the shortest path
    from ANY table already in that set. This keeps the result close to a
    minimal connecting subgraph without solving the full (NP-hard) Steiner
    tree problem, which would be overkill at this schema size.
    """
    retrieved = list(dict.fromkeys(retrieved_tables))  # de-dupe, keep order
    known = [t for t in retrieved if t in graph.adjacency]

    connected: list[str] = known[:1]
    bridge_tables: list[str] = []
    paths_used: list[list[str]] = []
    unreachable_pairs: list[tuple[str, str]] = []

    for table in known[1:]:
        if table in connected:
            continue

        best_path = None
        for anchor in connected:
            path = graph.shortest_path(anchor, table)
            if path is not None and (best_path is None or len(path) < len(best_path)):
                best_path = path

        if best_path is None:
            unreachable_pairs.append((connected[0], table))
            logger.info("No FK path found connecting '%s' to '%s'.", connected[0], table)
            connected.append(table)  # still part of the final set, just not bridgeable
            continue

        paths_used.append(best_path)
        new_bridges = [n for n in best_path if n not in retrieved and n not in bridge_tables]
        bridge_tables.extend(new_bridges)
        for node in best_path:
            if node not in connected:
                connected.append(node)

        if new_bridges:
            logger.info("Graph-expanded %s via path: %s", new_bridges, " -> ".join(best_path))

    unknown = [t for t in retrieved if t not in graph.adjacency]
    final_tables = list(dict.fromkeys(retrieved + bridge_tables + unknown))

    return ExpansionResult(
        retrieved_tables=retrieved,
        bridge_tables=bridge_tables,
        final_tables=final_tables,
        paths_used=paths_used,
        unreachable_pairs=unreachable_pairs,
    )
